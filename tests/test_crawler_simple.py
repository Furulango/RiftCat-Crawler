#!/usr/bin/env python3
"""
Script simplificado para probar el crawler de League of Legends enfocado en partidas ranked.
Solo busca partidas de ranked solo queue (420) y flex queue (440).

Uso:
    python test_crawler_ranked.py --seed-players "name1#tag1:region1" "name2#tag2:region2"
        --api-key "RGAPI-xxxx" --duration 10 --max-depth 2 --match-limit 5
"""

import os
import sys
import asyncio
import argparse
import time
import json
from typing import Dict, List, Set, Any, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import logging
import httpx
from httpx import AsyncClient, HTTPStatusError, RequestError

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("crawler_ranked")

# Configuración adicional para ver detalles de las solicitudes HTTP
logging.getLogger("httpx").setLevel(logging.INFO)

# Mapeo de regiones a plataformas
REGION_TO_PLATFORM = {
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

# APIs que usan URLs de plataforma regional
PLATFORM_APIS = ["match/v5", "riot/account/v1"]

# Tipos de colas ranked
RANKED_QUEUE_TYPES = {
    420: "RANKED_SOLO_DUO",
    440: "RANKED_FLEX"
}


# Enumeración para el estado de procesamiento
class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# Clase para limitar la tasa de solicitudes a la API
class RateLimiter:
    """Limita las solicitudes a la API para evitar exceder los límites."""
    
    def __init__(self, requests_per_second: int = 20, requests_per_minute: int = 100):
        self.requests_per_second = requests_per_second
        self.requests_per_minute = requests_per_minute
        
        # Semáforos para controlar concurrencia
        self.second_semaphore = asyncio.Semaphore(requests_per_second)
        self.minute_semaphore = asyncio.Semaphore(requests_per_minute)
        
        # Historial de solicitudes
        self.request_timestamps: List[float] = []
        
        # Bloqueo para acceso seguro a request_timestamps
        self.lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Adquiere un token antes de hacer una solicitud."""
        # Limitación por segundo
        await self.second_semaphore.acquire()
        
        # Limitación por minuto
        await self.minute_semaphore.acquire()
        
        # Registra la marca de tiempo actual
        current_time = time.time()
        async with self.lock:
            self.request_timestamps.append(current_time)
            
            # Limpia marcas de tiempo antiguas (> 1 minuto)
            self.request_timestamps = [t for t in self.request_timestamps 
                                      if current_time - t < 60]
    
    def release(self) -> None:
        """Libera un token después de una solicitud."""
        self.second_semaphore.release()
        
        # Programa la liberación del semáforo de minuto después de 60 segundos
        asyncio.create_task(self._delayed_release_minute())
    
    async def _delayed_release_minute(self) -> None:
        """Libera un token de minuto después de 60 segundos."""
        await asyncio.sleep(60)
        self.minute_semaphore.release()
    
    async def wait_if_needed(self) -> None:
        """Espera si estamos cerca del límite."""
        if self.second_semaphore._value <= 2:  # Si quedan pocos tokens
            await asyncio.sleep(1)  # Espera 1 segundo


# Cliente para la API de Riot Games
class RiotAPI:
    """Cliente asíncrono para la API de Riot Games."""
    
    def __init__(
        self, 
        api_key: str,
        default_region: str = "na1",
        timeout: int = 10,
        max_retries: int = 3,
        requests_per_second: int = 20,
        requests_per_minute: int = 100
    ):
        """Inicializa el cliente de la API de Riot.
        
        Args:
            api_key: Clave de API de Riot
            default_region: Región por defecto para solicitudes
            timeout: Tiempo de espera en segundos
            max_retries: Número máximo de reintentos para solicitudes fallidas
            requests_per_second: Límite de solicitudes por segundo
            requests_per_minute: Límite de solicitudes por minuto
        """
        self.api_key = api_key
        self.default_region = default_region
        self.timeout = timeout
        self.max_retries = max_retries
        
        # Cliente HTTP
        self.client = AsyncClient(
            timeout=timeout,
            headers={"X-Riot-Token": self.api_key}
        )
        
        # Limitador de tasa
        self.rate_limiter = RateLimiter(
            requests_per_second=requests_per_second,
            requests_per_minute=requests_per_minute
        )
    
    def _get_base_url(self, api_path: str, region: str) -> str:
        """Determina la URL base correcta según la API y la región.
        
        Args:
            api_path: Ruta de la API (por ejemplo, 'match/v5')
            region: Código de región (por ejemplo, 'na1', 'euw1')
            
        Returns:
            URL base para el endpoint
        """
        # Determina si usamos URL de plataforma o región
        uses_platform = any(api_path.startswith(platform_api) for platform_api in PLATFORM_APIS)
        
        if uses_platform:
            # Obtiene la plataforma correspondiente a la región
            platform = REGION_TO_PLATFORM.get(region, "americas")
            
            # Manejo especial para la API de cuenta
            if api_path.startswith("riot/account/v1"):
                return f"https://{platform}.api.riotgames.com/"
            return f"https://{platform}.api.riotgames.com/lol/"
        else:
            # Usa URL específica de región
            return f"https://{region}.api.riotgames.com/lol/"
    
    async def _request(
        self, 
        method: str, 
        endpoint: str, 
        region: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        api_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Realiza una solicitud HTTP a la API de Riot con manejo de errores y reintentos.
        
        Args:
            method: Método HTTP ('GET', 'POST', etc.)
            endpoint: Endpoint específico de la API
            region: Código de región para la solicitud
            params: Parámetros de consulta
            api_path: Ruta de la API para determinar la URL base
            
        Returns:
            Respuesta JSON como diccionario
            
        Raises:
            HTTPStatusError: Si la solicitud falla después de reintentos
        """
        region = region or self.default_region
        api_path = api_path or endpoint.split('/')[0]
        base_url = self._get_base_url(api_path, region)
        url = f"{base_url}{endpoint}"
        
        # Inicializa contadores
        retries = 0
        backoff = 1  # Segundos iniciales de espera para backoff exponencial
        
        while retries <= self.max_retries:
            try:
                # Adquiere token del limitador de tasa
                await self.rate_limiter.acquire()
                
                try:
                    # Realiza solicitud HTTP
                    response = await self.client.request(
                        method=method, 
                        url=url, 
                        params=params
                    )
                    
                    # Libera token después de la solicitud
                    self.rate_limiter.release()
                    
                    # Verifica si la respuesta fue exitosa
                    response.raise_for_status()
                    
                    # Devuelve datos JSON
                    return response.json()
                
                except HTTPStatusError as e:
                    # Maneja diferentes códigos de estado
                    if e.response.status_code == 429:  # Límite de tasa excedido
                        retry_after = int(e.response.headers.get("Retry-After", backoff))
                        logger.warning(f"Límite de tasa excedido. Esperando {retry_after} segundos.")
                        await asyncio.sleep(retry_after)
                        retries += 1
                        backoff *= 2  # Backoff exponencial
                    
                    elif e.response.status_code == 404:  # No encontrado
                        logger.warning(f"Recurso no encontrado: {url}")
                        return {}  # Devuelve diccionario vacío para manejo más fácil
                    
                    elif e.response.status_code >= 500:  # Error del servidor
                        if retries < self.max_retries:
                            await asyncio.sleep(backoff)
                            retries += 1
                            backoff *= 2
                        else:
                            logger.error(f"Error del servidor después de {retries} intentos: {url}")
                            raise
                    
                    else:  # Otros errores HTTP
                        logger.error(f"Error HTTP {e.response.status_code}: {url}")
                        # Para errores 400, puede ser útil ver el contenido de la respuesta
                        if e.response.status_code == 400:
                            try:
                                content = e.response.text
                                logger.error(f"Contenido de error 400: {content}")
                            except:
                                pass
                        raise
                
                except RequestError as e:
                    # Errores de red, tiempos de espera, etc.
                    if retries < self.max_retries:
                        await asyncio.sleep(backoff)
                        retries += 1
                        backoff *= 2
                    else:
                        logger.error(f"Error de solicitud después de {retries} intentos: {str(e)}")
                        raise
            
            finally:
                # Verifica si necesitamos esperar antes de la próxima solicitud
                await self.rate_limiter.wait_if_needed()
        
        # Si llegamos aquí, los reintentos se agotaron
        raise HTTPStatusError(f"Reintentos agotados para {url}", request=None, response=None)
    
    # ========== Métodos para endpoints específicos ==========
    
    async def get_account_by_puuid(self, puuid: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene datos de cuenta por PUUID.
        
        Args:
            puuid: PUUID del jugador
            region: Código de región
            
        Returns:
            Datos de la cuenta
        """
        endpoint = f"riot/account/v1/accounts/by-puuid/{puuid}"
        return await self._request("GET", endpoint, region=region, api_path="riot/account/v1")
    
    async def get_account_by_riot_id(
        self, 
        game_name: str, 
        tag_line: str, 
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Obtiene datos de cuenta por Riot ID (nombre de juego y línea de etiqueta).
        
        Args:
            game_name: Nombre de juego del jugador
            tag_line: Línea de etiqueta del jugador
            region: Código de región
            
        Returns:
            Datos de la cuenta
        """
        endpoint = f"riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        return await self._request("GET", endpoint, region=region, api_path="riot/account/v1")
    
    async def get_summoner_by_name_tag(
        self, 
        game_name: str, 
        tag_line: str, 
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Obtiene datos de invocador por nombre de juego y línea de etiqueta.
        
        Args:
            game_name: Nombre de juego del invocador
            tag_line: Línea de etiqueta del invocador
            region: Código de región
            
        Returns:
            Datos del invocador
        """
        # Primero obtiene el PUUID usando la API de cuenta
        account_data = await self.get_account_by_riot_id(game_name, tag_line, region)
        
        if not account_data or 'puuid' not in account_data:
            logger.warning(f"No se pudo encontrar la cuenta para {game_name}#{tag_line}")
            return {}
        
        # Luego obtiene los datos del invocador usando el PUUID
        puuid = account_data.get('puuid')
        summoner_data = await self.get_summoner_by_puuid(puuid, region)
        
        # Enriquece los datos del invocador con el nombre de juego y la línea de etiqueta
        if summoner_data:
            summoner_data['gameName'] = game_name
            summoner_data['tagLine'] = tag_line
        
        return summoner_data
    
    async def get_summoner_by_puuid(self, puuid: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene datos de invocador por PUUID.
        
        Args:
            puuid: PUUID del invocador
            region: Código de región
            
        Returns:
            Datos del invocador
        """
        endpoint = f"summoner/v4/summoners/by-puuid/{puuid}"
        return await self._request("GET", endpoint, region=region, api_path="summoner/v4")
    
    async def get_ranked_matches_by_puuid(
        self, 
        puuid: str, 
        region: Optional[str] = None,
        count: int = 20,
        days_back: int = 120
    ) -> List[Dict[str, str]]:
        """Obtiene IDs de partidas ranked recientes para un invocador.
        
        Args:
            puuid: PUUID del invocador
            region: Código de región
            count: Número de partidas a recuperar (máx. 100)
            days_back: Días hacia atrás para buscar partidas
            
        Returns:
            Lista con pares de (ID de partida, tipo de cola)
        """
        matches_by_type = []
        three_months_ago = datetime.now() - timedelta(days=90)
        start_time = int(three_months_ago.timestamp())
        
        # Intentar obtener partidas para cada tipo de cola ranked
        for queue_id, queue_name in RANKED_QUEUE_TYPES.items():
            try:
                # Intentar obtener partidas de este tipo de cola específico
                endpoint = f"match/v5/matches/by-puuid/{puuid}/ids"
                params = {
                    "count": min(count, 100), 
                    "queue": queue_id,
                    "startTime": start_time
                }
                
                logger.debug(f"Buscando partidas {queue_name} para {puuid}")
                
                result = await self._request(
                    "GET", 
                    endpoint, 
                    region=region, 
                    params=params,
                    api_path="match/v5"
                )
                
                # La respuesta es una lista de strings (IDs)
                if isinstance(result, list) and result:
                    for match_id in result:
                        matches_by_type.append({
                            "match_id": match_id,
                            "queue_type": queue_name
                        })
                    logger.info(f"Se encontraron {len(result)} partidas {queue_name}")
                else:
                    logger.info(f"No se encontraron partidas {queue_name}")
                
                # Pequeño retraso para evitar problemas de tasa
                await asyncio.sleep(0.2)
            
            except Exception as e:
                logger.warning(f"Error obteniendo partidas {queue_name}: {str(e)}")
                continue
        
        # Si no encontramos nada, intentar sin filtros y luego filtrar por nuestro lado
        if not matches_by_type:
            try:
                logger.info(f"Intentando obtener partidas sin filtros para {puuid}")
                endpoint = f"match/v5/matches/by-puuid/{puuid}/ids"
                params = {
                    "count": min(count * 2, 100),  # Pedimos más para aumentar la probabilidad
                    "startTime": start_time
                }
                
                result = await self._request(
                    "GET", 
                    endpoint, 
                    region=region, 
                    params=params,
                    api_path="match/v5"
                )
                
                if isinstance(result, list) and result:
                    for match_id in result:
                        # Asignaremos el tipo después de obtener los detalles
                        matches_by_type.append({
                            "match_id": match_id,
                            "queue_type": "UNKNOWN"  # Temporal
                        })
                    logger.info(f"Se encontraron {len(result)} partidas sin filtro")
            except Exception as e:
                logger.warning(f"Error obteniendo partidas sin filtro: {str(e)}")
        
        # Limitar al número deseado
        return matches_by_type[:count]
    
    async def get_match_detail(self, match_id: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene detalles completos de una partida.
        
        Args:
            match_id: ID de la partida
            region: Código de región
            
        Returns:
            Datos completos de la partida
        """
        endpoint = f"match/v5/matches/{match_id}"
        return await self._request("GET", endpoint, region=region, api_path="match/v5")
    
    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        if self.client:
            await self.client.aclose()


# Clase para representar a un invocador en memoria
class MemorySummoner:
    """Representa a un invocador almacenado en memoria."""
    
    def __init__(
        self,
        id: str,
        puuid: str,
        name: str,
        game_name: str,
        tag_line: str,
        region: str,
        summoner_level: int,
        processing_depth: int = 0
    ):
        self.id = id
        self.puuid = puuid
        self.name = name
        self.game_name = game_name
        self.tag_line = tag_line
        self.region = region
        self.summoner_level = summoner_level
        self.processing_status = ProcessingStatus.PENDING
        self.processing_depth = processing_depth
        self.matches_analyzed = 0
        self.related_summoners: Set[str] = set()  # IDs de invocadores relacionados
        self.last_updated = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el objeto a un diccionario."""
        return {
            "id": self.id,
            "puuid": self.puuid,
            "name": self.name,
            "game_name": self.game_name,
            "tag_line": self.tag_line,
            "region": self.region,
            "summoner_level": self.summoner_level,
            "processing_status": self.processing_status,
            "processing_depth": self.processing_depth,
            "matches_analyzed": self.matches_analyzed,
            "last_updated": self.last_updated.isoformat()
        }


# Clase para representar una partida en memoria
class MemoryMatch:
    """Representa una partida almacenada en memoria."""
    
    def __init__(
        self,
        id: str,
        game_id: int,
        game_creation: int,
        game_duration: int,
        game_version: str,
        platform_id: str,
        queue_id: int,
        queue_type: str,
        participants: List[Dict[str, Any]]
    ):
        self.id = id
        self.game_id = game_id
        self.game_creation = game_creation
        self.game_duration = game_duration
        self.game_version = game_version
        self.platform_id = platform_id
        self.queue_id = queue_id
        self.queue_type = queue_type  # RANKED_SOLO_DUO o RANKED_FLEX
        self.participants = participants
        self.processing_status = ProcessingStatus.COMPLETED
        self.last_updated = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el objeto a un diccionario."""
        return {
            "id": self.id,
            "game_id": self.game_id,
            "game_creation": self.game_creation,
            "game_duration": self.game_duration,
            "game_version": self.game_version,
            "platform_id": self.platform_id,
            "queue_id": self.queue_id,
            "queue_type": self.queue_type,
            "processing_status": self.processing_status,
            "last_updated": self.last_updated.isoformat()
        }


# Gestor de memoria para almacenar datos en lugar de base de datos
class MemoryStore:
    """Almacena datos en memoria en lugar de usar una base de datos."""
    
    def __init__(self):
        self.summoners: Dict[str, MemorySummoner] = {}  # Mapa de ID a invocador
        self.summoners_by_puuid: Dict[str, MemorySummoner] = {}  # Mapa de PUUID a invocador
        self.matches: Dict[str, MemoryMatch] = {}  # Mapa de ID a partida
        self.match_summoners: Dict[str, Set[str]] = {}  # Mapeo de ID de partida a conjunto de IDs de invocadores
        self.summoner_matches: Dict[str, Set[str]] = {}  # Mapeo de ID de invocador a conjunto de IDs de partidas
    
    def add_or_update_summoner(
        self, 
        data: Dict[str, Any], 
        region: str,
        game_name: Optional[str] = None,
        tag_line: Optional[str] = None,
        processing_depth: int = 0
    ) -> Optional[MemorySummoner]:
        """Agrega o actualiza un invocador en la memoria.
        
        Args:
            data: Datos del invocador de la API de Riot
            region: Región del invocador
            game_name: Nombre de juego del Riot ID
            tag_line: Línea de etiqueta del Riot ID
            processing_depth: Profundidad de procesamiento
            
        Returns:
            Invocador creado o actualizado
        """
        puuid = data.get('puuid')
        if not puuid:
            logger.error("Los datos del invocador no contienen PUUID")
            return None
        
        id = data.get('id')
        if not id:
            logger.error("Los datos del invocador no contienen ID")
            return None
        
        # Usar el valor proporcionado o el de los datos
        game_name = game_name or data.get('gameName')
        tag_line = tag_line or data.get('tagLine')
        
        # Verificar si ya existe por PUUID
        if puuid in self.summoners_by_puuid:
            summoner = self.summoners_by_puuid[puuid]
            
            # Actualizar los campos
            summoner.id = id
            summoner.name = data.get('name', summoner.name)
            summoner.summoner_level = data.get('summonerLevel', summoner.summoner_level)
            summoner.region = region
            
            if game_name:
                summoner.game_name = game_name
            if tag_line:
                summoner.tag_line = tag_line
                
            summoner.last_updated = datetime.now()
            
            # Asegurarse de que está mapeado por ID
            self.summoners[id] = summoner
        else:
            # Crear nuevo invocador
            summoner = MemorySummoner(
                id=id,
                puuid=puuid,
                name=data.get('name', 'Unknown'),
                game_name=game_name or 'Unknown',
                tag_line=tag_line or 'Unknown',
                region=region,
                summoner_level=data.get('summonerLevel', 0),
                processing_depth=processing_depth
            )
            
            # Almacenar en mapas
            self.summoners[id] = summoner
            self.summoners_by_puuid[puuid] = summoner
            self.summoner_matches[id] = set()
        
        return summoner
    
    def add_match(
        self, 
        match_data: Dict[str, Any],
        queue_type: Optional[str] = None
    ) -> Optional[MemoryMatch]:
        """Agrega una partida a la memoria.
        
        Args:
            match_data: Datos de la partida de la API de Riot
            queue_type: Tipo de cola (RANKED_SOLO_DUO o RANKED_FLEX)
            
        Returns:
            Partida creada
        """
        # Extraer metadata e info
        metadata = match_data.get('metadata', {})
        info = match_data.get('info', {})
        
        match_id = metadata.get('matchId')
        if not match_id:
            logger.error("Los datos de la partida no contienen ID")
            return None
        
        # Verificar si ya existe
        if match_id in self.matches:
            logger.debug(f"Partida ya existe: {match_id}")
            return self.matches[match_id]
        
        # Determinar el tipo de cola
        queue_id = info.get('queueId', 0)
        if not queue_type:
            queue_type = RANKED_QUEUE_TYPES.get(queue_id, "UNKNOWN")
            
        # Solo procesar partidas ranked
        if queue_type not in RANKED_QUEUE_TYPES.values() and queue_id not in RANKED_QUEUE_TYPES:
            logger.debug(f"Saltando partida no ranked: {match_id} (cola {queue_id})")
            return None
        
        # Crear partida
        match = MemoryMatch(
            id=match_id,
            game_id=info.get('gameId', 0),
            game_creation=info.get('gameCreation', 0),
            game_duration=info.get('gameDuration', 0),
            game_version=info.get('gameVersion', ''),
            platform_id=info.get('platformId', ''),
            queue_id=queue_id,
            queue_type=queue_type,
            participants=info.get('participants', [])
        )
        
        # Almacenar en mapa
        self.matches[match_id] = match
        self.match_summoners[match_id] = set()
        
        return match
    
    def add_summoner_to_match(self, match_id: str, summoner_id: str) -> None:
        """Asocia un invocador a una partida.
        
        Args:
            match_id: ID de la partida
            summoner_id: ID del invocador
        """
        if match_id in self.match_summoners:
            self.match_summoners[match_id].add(summoner_id)
        else:
            self.match_summoners[match_id] = {summoner_id}
            
        if summoner_id in self.summoner_matches:
            self.summoner_matches[summoner_id].add(match_id)
        else:
            self.summoner_matches[summoner_id] = {match_id}
    
    def add_relation(self, source_id: str, target_id: str) -> None:
        """Agrega una relación entre dos invocadores.
        
        Args:
            source_id: ID del invocador fuente
            target_id: ID del invocador objetivo
        """
        if source_id in self.summoners and target_id in self.summoners:
            self.summoners[source_id].related_summoners.add(target_id)
    
    def get_summoner_by_id(self, summoner_id: str) -> Optional[MemorySummoner]:
        """Obtiene un invocador por ID.
        
        Args:
            summoner_id: ID del invocador
            
        Returns:
            Invocador si se encuentra, None en caso contrario
        """
        return self.summoners.get(summoner_id)
    
    def get_summoner_by_puuid(self, puuid: str) -> Optional[MemorySummoner]:
        """Obtiene un invocador por PUUID.
        
        Args:
            puuid: PUUID del invocador
            
        Returns:
            Invocador si se encuentra, None en caso contrario
        """
        return self.summoners_by_puuid.get(puuid)
    
    def get_match(self, match_id: str) -> Optional[MemoryMatch]:
        """Obtiene una partida por ID.
        
        Args:
            match_id: ID de la partida
            
        Returns:
            Partida si se encuentra, None en caso contrario
        """
        return self.matches.get(match_id)
    
    def get_pending_summoners(self, limit: int = 10, max_depth: int = 2) -> List[MemorySummoner]:
        """Obtiene invocadores pendientes de procesamiento.
        
        Args:
            limit: Número máximo de invocadores a devolver
            max_depth: Profundidad máxima a considerar
            
        Returns:
            Lista de invocadores pendientes
        """
        pending = [
            s for s in self.summoners.values()
            if s.processing_status == ProcessingStatus.PENDING and s.processing_depth <= max_depth
        ]
        
        # Ordenar por profundidad
        pending.sort(key=lambda s: s.processing_depth)
        
        return pending[:limit]
    
    def update_summoner_status(
        self, 
        summoner_id: str, 
        status: ProcessingStatus,
        matches_analyzed: Optional[int] = None,
        increment_depth: bool = False
    ) -> bool:
        """Actualiza el estado de procesamiento de un invocador.
        
        Args:
            summoner_id: ID del invocador
            status: Nuevo estado de procesamiento
            matches_analyzed: Número de partidas analizadas (si se proporciona)
            increment_depth: Si se debe incrementar la profundidad de procesamiento
            
        Returns:
            True si se actualizó correctamente, False en caso contrario
        """
        summoner = self.get_summoner_by_id(summoner_id)
        if not summoner:
            return False
        
        summoner.processing_status = status
        
        if matches_analyzed is not None:
            summoner.matches_analyzed = matches_analyzed
            
        if increment_depth:
            summoner.processing_depth += 1
            
        summoner.last_updated = datetime.now()
        
        return True
    
    def get_metrics(self) -> Dict[str, Any]:
        """Obtiene métricas del crawler.
        
        Returns:
            Diccionario con métricas actuales
        """
        total_summoners = len(self.summoners)
        completed = sum(1 for s in self.summoners.values() if s.processing_status == ProcessingStatus.COMPLETED)
        pending = sum(1 for s in self.summoners.values() if s.processing_status == ProcessingStatus.PENDING)
        processing = sum(1 for s in self.summoners.values() if s.processing_status == ProcessingStatus.PROCESSING)
        failed = sum(1 for s in self.summoners.values() if s.processing_status == ProcessingStatus.FAILED)
        
        # Obtener profundidad máxima
        current_depth = max(
            (s.processing_depth for s in self.summoners.values()),
            default=0
        )
        
        # Contar partidas por tipo
        matches_by_type = {}
        for match in self.matches.values():
            queue_type = match.queue_type
            matches_by_type[queue_type] = matches_by_type.get(queue_type, 0) + 1
        
        return {
            "total_summoners": total_summoners,
            "completed": completed,
            "pending": pending,
            "processing": processing,
            "failed": failed,
            "total_matches": len(self.matches),
            "matches_by_type": matches_by_type,
            "current_depth": current_depth,
            "timestamp": datetime.now().isoformat()
        }


# Controlador simplificado del crawler (enfocado en partidas ranked)
class RankedCrawlerController:
    """Controlador del crawler simplificado que funciona en memoria y se enfoca en partidas ranked."""
    
    def __init__(
        self,
        api_key: str,
        match_limit: int = 5,
        max_depth: int = 2,
        batch_size: int = 10
    ):
        """Inicializa el controlador del crawler.
        
        Args:
            api_key: Clave de API de Riot
            match_limit: Número máximo de partidas a obtener por invocador
            max_depth: Profundidad máxima para el grafo del crawler
            batch_size: Tamaño del lote para procesamiento
        """
        self.match_limit = match_limit
        self.max_depth = max_depth
        self.batch_size = batch_size
        
        # Crear cliente de API
        self.riot_api = RiotAPI(api_key=api_key)
        
        # Almacenamiento en memoria
        self.store = MemoryStore()
        
        # Estado
        self.running = False
        self.stop_requested = False
    
    async def add_seed_summoner(self, summoner_name: str, region: str) -> Optional[MemorySummoner]:
        """Agrega un invocador semilla para iniciar el crawl.
        
        Args:
            summoner_name: Nombre del invocador (debe estar en el formato "name#tag")
            region: Código de región
            
        Returns:
            Invocador agregado o None si falló
        """
        try:
            # Verificar que el formato sea correcto (name#tag)
            if '#' not in summoner_name:
                logger.error(f"Formato de invocador no válido: {summoner_name}. Debe usar el formato 'name#tag'.")
                return None
            
            # Extraer game_name y tag_line
            game_name, tag_line = summoner_name.split('#', 1)
            logger.info(f"Agregando invocador semilla: {game_name}#{tag_line} en {region}")
            
            # Obtener datos del invocador de la API usando Riot ID
            summoner_data = await self.riot_api.get_summoner_by_name_tag(game_name, tag_line, region)
            
            if not summoner_data:
                logger.error(f"No se pudieron obtener datos del invocador {summoner_name} en {region}")
                return None
            
            # Agregar a la memoria
            summoner = self.store.add_or_update_summoner(
                summoner_data, region, game_name=game_name, tag_line=tag_line
            )
            
            if not summoner:
                logger.error(f"No se pudo agregar el invocador semilla {summoner_name}")
                return None
            
            display_name = f"{summoner.game_name}#{summoner.tag_line}"
            logger.info(f"Se agregó el invocador semilla: {display_name} ({summoner.id})")
            return summoner
        
        except Exception as e:
            logger.error(f"Error al agregar el invocador semilla {summoner_name}: {str(e)}")
            return None
    
    async def process_summoner(self, summoner_id: str, region: str) -> bool:
        """Procesa un invocador obteniendo sus partidas recientes.
        
        Args:
            summoner_id: ID del invocador
            region: Código de región
            
        Returns:
            True si fue exitoso, False en caso contrario
        """
        try:
            logger.info(f"Procesando invocador {summoner_id} en región {region}")
            
            # Obtener invocador de la memoria
            summoner = self.store.get_summoner_by_id(summoner_id)
            if not summoner:
                logger.error(f"Invocador no encontrado: {summoner_id}")
                return False
            
            # Marcar como procesando
            self.store.update_summoner_status(summoner_id, ProcessingStatus.PROCESSING)
            
            # Obtener partidas ranked para el invocador
            matches_data = await self.riot_api.get_ranked_matches_by_puuid(
                puuid=summoner.puuid,
                region=region,
                count=self.match_limit,
                days_back=120  # Buscar en los últimos 4 meses
            )
            
            if not matches_data:
                logger.info(f"No se encontraron partidas ranked para el invocador {summoner_id}")
                self.store.update_summoner_status(
                    summoner_id, ProcessingStatus.COMPLETED, 
                    matches_analyzed=0, increment_depth=True
                )
                return True
            
            # Procesar cada partida
            processed_count = 0
            for match_info in matches_data:
                try:
                    match_id = match_info["match_id"]
                    queue_type = match_info["queue_type"]
                    
                    # Verificar si la partida ya existe
                    existing_match = self.store.get_match(match_id)
                    if existing_match:
                        logger.debug(f"Partida ya procesada: {match_id}")
                        processed_count += 1
                        # Asegurarse de que el invocador esté relacionado con la partida
                        self.store.add_summoner_to_match(match_id, summoner_id)
                        continue
                    
                    # Obtener detalles de la partida
                    match_data = await self.riot_api.get_match_detail(match_id, region)
                    if not match_data:
                        logger.warning(f"No se pudieron obtener datos de la partida: {match_id}")
                        continue
                    
                    # Guardar partida en memoria
                    match = self.store.add_match(match_data, queue_type)
                    if not match:
                        # Puede ser None si la partida no es ranked
                        continue
                    
                    # Relacionar invocador con la partida
                    self.store.add_summoner_to_match(match_id, summoner_id)
                    
                    processed_count += 1
                    
                    # Agregar relaciones de invocadores - todos los jugadores en esta partida están relacionados
                    participants = match_data.get('info', {}).get('participants', [])
                    for participant in participants:
                        participant_puuid = participant.get('puuid')
                        if participant_puuid and participant_puuid != summoner.puuid:
                            # Verificar si el invocador ya existe
                            related_summoner = self.store.get_summoner_by_puuid(participant_puuid)
                            
                            # Si no existe, crearlo como un placeholder
                            if not related_summoner:
                                # Intentar obtener nombre de juego y línea de etiqueta
                                game_name = participant.get('riotIdGameName')
                                tag_line = participant.get('riotIdTagline')
                                
                                if game_name and tag_line:
                                    # Crear placeholder con profundidad incrementada
                                    participant_data = {
                                        'puuid': participant_puuid,
                                        'id': participant.get('summonerId', f"placeholder_{participant_puuid[:8]}"),
                                        'name': participant.get('summonerName', 'Unknown'),
                                        'summonerLevel': 0  # Placeholder
                                    }
                                    
                                    related_summoner = self.store.add_or_update_summoner(
                                        participant_data, 
                                        region=match.platform_id[:4] if match.platform_id else region,
                                        game_name=game_name,
                                        tag_line=tag_line,
                                        processing_depth=summoner.processing_depth + 1
                                    )
                            
                            # Si tenemos un invocador relacionado, agregar la relación
                            if related_summoner:
                                self.store.add_relation(summoner.id, related_summoner.id)
                    
                    # Pequeño retraso entre partidas para evitar problemas de tasa
                    await asyncio.sleep(0.1)
                
                except Exception as e:
                    logger.error(f"Error al procesar la partida {match_id}: {str(e)}")
            
            # Marcar al invocador como completado e incrementar profundidad
            logger.info(f"Completado el procesamiento del invocador {summoner_id}, procesadas {processed_count} partidas")
            self.store.update_summoner_status(
                summoner_id, ProcessingStatus.COMPLETED, 
                matches_analyzed=processed_count, increment_depth=True
            )
            
            return True
        
        except Exception as e:
            logger.error(f"Error al procesar el invocador {summoner_id}: {str(e)}")
            
            # Marcar como fallido
            self.store.update_summoner_status(summoner_id, ProcessingStatus.FAILED)
            
            return False
    
    async def run_batch(self) -> int:
        """Procesa un lote de invocadores pendientes.
        
        Returns:
            Número de invocadores procesados
        """
        # Obtener invocadores pendientes
        summoners = self.store.get_pending_summoners(limit=self.batch_size, max_depth=self.max_depth)
        
        if not summoners:
            logger.info("No se encontraron invocadores pendientes")
            return 0
        
        logger.info(f"Procesando lote de {len(summoners)} invocadores")
        
        # Procesar invocadores en paralelo
        tasks = []
        for summoner in summoners:
            task = asyncio.create_task(
                self.process_summoner(summoner.id, summoner.region)
            )
            tasks.append(task)
        
        # Esperar a que todas las tareas se completen
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Contar completados exitosamente
        successful = sum(1 for r in results if r is True)
        logger.info(f"Lote completado: {successful}/{len(summoners)} exitosos")
        
        return len(summoners)
    
    async def start(self) -> bool:
        """Inicia el crawler.
        
        Returns:
            True si se inició correctamente
        """
        if self.running:
            logger.warning("El crawler ya está en ejecución")
            return False
        
        # Restablecer bandera de parada
        self.stop_requested = False
        self.running = True
        
        logger.info("Crawler iniciado correctamente")
        return True
    
    async def stop(self) -> None:
        """Detiene el crawler."""
        if not self.running:
            logger.warning("El crawler no está en ejecución")
            return
        
        logger.info("Deteniendo crawler...")
        self.stop_requested = True
        self.running = False
        
        # Cerrar cliente de API
        await self.riot_api.close()
        
        logger.info("Crawler detenido")
    
    async def get_status(self) -> Dict[str, Any]:
        """Obtiene el estado actual del crawler.
        
        Returns:
            Diccionario con estado
        """
        metrics = self.store.get_metrics()
        
        status = {
            "running": self.running,
            "metrics": metrics,
            "config": {
                "match_limit": self.match_limit,
                "max_depth": self.max_depth,
                "batch_size": self.batch_size
            }
        }
        
        return status


async def run_test_crawl(
    seed_players: List[tuple],
    api_key: str,
    duration_minutes: int = 10,
    max_depth: int = 2,
    match_limit: int = 5,
    batch_size: int = 5
) -> Dict[str, Any]:
    """Ejecuta un crawl de prueba durante una duración especificada.
    
    Args:
        seed_players: Lista de tuplas (name, region)
        api_key: Clave de API de Riot
        duration_minutes: Cuánto tiempo ejecutar el crawl
        max_depth: Profundidad máxima para el crawler
        match_limit: Máximo de partidas por invocador
        batch_size: Tamaño del lote para procesamiento
        
    Returns:
        Diccionario con resultados
    """
    logger.info(f"Iniciando crawl de prueba con {len(seed_players)} invocadores semilla")
    logger.info(f"Duración: {duration_minutes} minutos, Profundidad máxima: {max_depth}")
    
    # Crear controlador con configuración personalizada
    controller = RankedCrawlerController(
        api_key=api_key,
        max_depth=max_depth,
        match_limit=match_limit,
        batch_size=batch_size
    )
    
    # Agregar invocadores semilla
    for name, region in seed_players:
        await controller.add_seed_summoner(name, region)
    
    # Iniciar el crawler
    await controller.start()
    
    try:
        # Hacer seguimiento de métricas a lo largo del tiempo
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        metrics_over_time = []
        
        while time.time() < end_time and controller.running:
            # Procesar un lote
            await controller.run_batch()
            
            # Obtener estado actual
            status = await controller.get_status()
            metrics = status["metrics"]
            
            # Registrar progreso
            logger.info(
                f"Progreso: Invocadores={metrics['total_summoners']} "
                f"(Completados={metrics['completed']}, Pendientes={metrics['pending']}), "
                f"Partidas={metrics['total_matches']}"
            )
            
            if 'matches_by_type' in metrics:
                match_types = ", ".join([f"{k}: {v}" for k, v in metrics['matches_by_type'].items()])
                logger.info(f"Partidas por tipo: {match_types}")
            
            # Guardar métricas
            metrics_over_time.append({
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": int(time.time() - start_time),
                **metrics
            })
            
            # Verificar finalización
            if metrics["pending"] == 0 and metrics["processing"] == 0:
                logger.info("Crawl completado antes de tiempo - no hay más invocadores pendientes")
                break
            
            # Esperar antes de verificar de nuevo
            await asyncio.sleep(10)
    
    finally:
        # Detener el crawler
        await controller.stop()
    
    # Obtener métricas finales
    final_status = await controller.get_status()
    final_metrics = final_status["metrics"]
    
    results = {
        "duration_seconds": int(time.time() - start_time),
        "summoners_total": final_metrics["total_summoners"],
        "matches_total": final_metrics["total_matches"],
        "summoners_completed": final_metrics["completed"],
        "summoners_failed": final_metrics["failed"],
        "summoners_pending": final_metrics["pending"],
        "max_depth_reached": final_metrics["current_depth"],
        "matches_by_type": final_metrics.get("matches_by_type", {}),
        "metrics_over_time": metrics_over_time
    }
    
    return results


def generate_report(results: Dict[str, Any]) -> str:
    """Genera un informe legible de los resultados del crawl.
    
    Args:
        results: Resultados del crawl
        
    Returns:
        Informe formateado
    """
    report = []
    report.append("===== INFORME DEL CRAWL DE RANKED =====")
    report.append(f"Duración: {results['duration_seconds']} segundos")
    report.append(f"Invocadores totales: {results['summoners_total']}")
    report.append(f"Partidas totales: {results['matches_total']}")
    report.append(f"Invocadores completados: {results['summoners_completed']}")
    report.append(f"Invocadores fallidos: {results['summoners_failed']}")
    report.append(f"Invocadores pendientes: {results['summoners_pending']}")
    report.append(f"Profundidad máxima alcanzada: {results['max_depth_reached']}")
    
    # Distribución de partidas por tipo de cola
    report.append("\nDistribución de partidas por tipo de cola:")
    for queue, count in results.get('matches_by_type', {}).items():
        report.append(f"  {queue}: {count}")
    
    # Métricas a lo largo del tiempo
    if results.get('metrics_over_time'):
        report.append("\nProgreso a lo largo del tiempo:")
        for i, metrics in enumerate(results['metrics_over_time']):
            elapsed = metrics.get('elapsed_seconds', 0)
            matches = metrics.get('total_matches', 0)
            matches_by_type = ""
            if 'matches_by_type' in metrics:
                matches_by_type = " - Tipos: " + ", ".join([f"{k}:{v}" for k, v in metrics['matches_by_type'].items()])
            
            report.append(
                f"  {i+1}. Tiempo: {elapsed}s - "
                f"Invocadores: {metrics.get('total_summoners', 0)} - "
                f"Partidas: {matches}{matches_by_type}"
            )
    
    return "\n".join(report)


def main():
    """Función principal."""
    parser = argparse.ArgumentParser(description="Ejecutar un crawl de prueba enfocado en partidas ranked")
    parser.add_argument(
        "--seed-players", 
        type=str,
        nargs="+",
        default=["Faker#KR1:kr", "Bjergsen#NA1:na1"],
        help="Invocadores semilla en formato nombre#tag:region"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        required=True,
        help="Clave de API de Riot Games"
    )
    parser.add_argument(
        "--duration", 
        type=int,
        default=10,
        help="Duración en minutos"
    )
    parser.add_argument(
        "--max-depth", 
        type=int,
        default=2,
        help="Profundidad máxima del grafo"
    )
    parser.add_argument(
        "--match-limit", 
        type=int,
        default=5,
        help="Máximo de partidas por invocador"
    )
    parser.add_argument(
        "--batch-size", 
        type=int,
        default=5,
        help="Tamaño del lote para procesamiento"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Archivo de salida para resultados (JSON)"
    )
    
    args = parser.parse_args()
    
    # Analizar invocadores semilla
    seed_players = []
    for player in args.seed_players:
        parts = player.split(":")
        if len(parts) == 2:
            name_with_tag = parts[0]
            region = parts[1]
            seed_players.append((name_with_tag, region))
        else:
            logger.error(f"Formato de invocador semilla no válido: {player}, se esperaba nombre#tag:region")
    
    if not seed_players:
        logger.error("No se proporcionaron invocadores semilla válidos")
        sys.exit(1)
    
    # Ejecutar el crawl
    results = asyncio.run(run_test_crawl(
        seed_players=seed_players,
        api_key=args.api_key,
        duration_minutes=args.duration,
        max_depth=args.max_depth,
        match_limit=args.match_limit,
        batch_size=args.batch_size
    ))
    
    # Generar y mostrar informe
    report = generate_report(results)
    print("\n" + report)
    
    # Escribir resultados a archivo si se solicita
    if args.output:
        # Guardar resultados JSON
        json_file = args.output
        with open(json_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        # Guardar informe de texto
        report_file = args.output.rsplit('.', 1)[0] + '_report.txt'
        with open(report_file, 'w') as f:
            f.write(report)
            
        logger.info(f"Resultados guardados en {json_file}")
        logger.info(f"Informe guardado en {report_file}")


if __name__ == "__main__":
    main()