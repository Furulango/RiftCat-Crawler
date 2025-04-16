###  **RiftCat**

**RiftCat** es una infraestructura escalable diseñada para la recolección masiva e inteligente de datos de partidas de *League of Legends*, usando la API oficial de Riot. El sistema está pensado para operar de forma autónoma, viral y distribuida, explorando de manera progresiva todo el ecosistema de invocadores a partir de nodos semilla.

---

###  Objetivos del Proyecto

- Construir un **crawler inteligente y viral** que expanda su alcance entre perfiles conectados por partidas.
- Crear un **grafo dinámico de invocadores**, persistente y actualizable.
- Recolectar, filtrar y almacenar datos relevantes de partidas y jugadores.
- Integrar esta solución como un módulo en un futuro sistema avanzado de análisis tipo *coach IA* para LoL.

---

### Tecnologías

- **Lenguaje**: Python
- **API**: Riot Games Developer API
- **Web framework**: FastAPI
- **Base de datos**: PostgreSQL
- **ORM**: SQLAlchemy
- **Cola de tareas**: Celery + Redis
- **Ejecución distribuida**: Enrutamiento viral a través de un crawler programado
- **Infraestructura**: Docker y docker-compose

---

### 📁 Arquitectura de Carpetas

```bash
RiftCat/
│
├── app/
│   ├── core/           # Configuración, logs, conexión a API
│   ├── crawler/        # Algoritmo viral, expansión del grafo
│   ├── workers/        # Tareas asincrónicas para recolectar partidas
│   ├── db/             # Modelos ORM, persistencia y conexión
│   └── api/            # FastAPI para exponer endpoints del sistema
│
├── scripts/            # Utilidades y scripts de prueba
├── data/               # Datos descargados (match JSON, logs, etc.)
├── tests/              # Pruebas automatizadas
│
├── .env                # Variables de entorno y credenciales
├── requirements.txt    # Dependencias
├── Dockerfile
├── docker-compose.yml
└── README.md
```


---

### 🧩 Características Clave

- **Crawleo viral**: El sistema explora nuevos perfiles automáticamente a través de las partidas recolectadas.
- **Expansión controlada**: Incluye filtros de calidad para evitar ruido y priorizar partidas útiles.
- **Diseño modular**: Cada componente (crawler, workers, persistencia, API) puede escalarse por separado.
- **Alta escalabilidad**: Compatible con arquitecturas distribuidas, pensado para operación continua.

---
