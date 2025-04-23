import asyncio
import time
from typing import Dict, List, Optional, Any, Callable, Coroutine
from datetime import datetime, timedelta

from app.core.logger import get_logger
from app.crawler.controller import CrawlerController

logger = get_logger(__name__)


class Task:
    """Representation of a scheduled task."""
    
    def __init__(
        self,
        name: str,
        coroutine: Callable[..., Coroutine],
        args: Optional[List[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        interval: int = 60,
        initial_delay: int = 0,
        max_retries: int = 3,
        retry_delay: int = 5
    ):
        """Initialize a scheduled task.
        
        Args:
            name: Task name
            coroutine: Async function to execute
            args: Positional arguments for the coroutine
            kwargs: Keyword arguments for the coroutine
            interval: Interval between executions in seconds
            initial_delay: Initial delay before first execution in seconds
            max_retries: Maximum number of retries on failure
            retry_delay: Delay between retries in seconds
        """
        self.name = name
        self.coroutine = coroutine
        self.args = args or []
        self.kwargs = kwargs or {}
        self.interval = interval
        self.initial_delay = initial_delay
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Task state
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.failures: int = 0
        self.task: Optional[asyncio.Task] = None
        self.running: bool = False
    
    async def execute(self) -> None:
        """Execute the task."""
        if self.running:
            logger.warning(f"Task {self.name} is already running, skipping execution")
            return
        
        self.running = True
        self.last_run = datetime.now()
        self.next_run = self.last_run + timedelta(seconds=self.interval)
        
        try:
            retries = 0
            while retries <= self.max_retries:
                try:
                    logger.info(f"Executing task {self.name}")
                    await self.coroutine(*self.args, **self.kwargs)
                    self.failures = 0  # Reset failure count on success
                    break
                
                except Exception as e:
                    retries += 1
                    self.failures += 1
                    
                    if retries <= self.max_retries:
                        logger.warning(f"Task {self.name} failed ({retries}/{self.max_retries}): {str(e)}")
                        await asyncio.sleep(self.retry_delay)
                    else:
                        logger.error(f"Task {self.name} failed after {self.max_retries} retries: {str(e)}")
                        raise
        
        finally:
            self.running = False


class CrawlerScheduler:
    """Scheduler for the crawler system."""
    
    def __init__(self, controller: Optional[CrawlerController] = None):
        """Initialize the scheduler.
        
        Args:
            controller: CrawlerController instance
        """
        self.controller = controller or CrawlerController()
        self.tasks: Dict[str, Task] = {}
        self.running = False
        self.stop_requested = False
    
    def add_task(self, task: Task) -> None:
        """Add a task to the scheduler.
        
        Args:
            task: Task to add
        """
        self.tasks[task.name] = task
        logger.info(f"Added task: {task.name}")
    
    def remove_task(self, task_name: str) -> bool:
        """Remove a task from the scheduler.
        
        Args:
            task_name: Name of the task to remove
            
        Returns:
            True if the task was removed
        """
        if task_name in self.tasks:
            task = self.tasks[task_name]
            if task.task and not task.task.done():
                task.task.cancel()
            
            del self.tasks[task_name]
            logger.info(f"Removed task: {task_name}")
            return True
        
        return False
    
    async def start(self) -> None:
        """Start the scheduler."""
        if self.running:
            logger.warning("Scheduler is already running")
            return
        
        self.running = True
        self.stop_requested = False
        
        # Start controller
        await self.controller.start()
        
        # Start scheduler loop
        asyncio.create_task(self._scheduler_loop())
        
        logger.info("Scheduler started")
    
    async def stop(self) -> None:
        """Stop the scheduler and all tasks."""
        if not self.running:
            logger.warning("Scheduler is not running")
            return
        
        logger.info("Stopping scheduler...")
        self.stop_requested = True
        self.running = False
        
        # Cancel all tasks
        for name, task in self.tasks.items():
            if task.task and not task.task.done():
                task.task.cancel()
        
        # Stop controller
        await self.controller.stop()
        
        logger.info("Scheduler stopped")
    
    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        try:
            # Apply initial delays
            for name, task in self.tasks.items():
                if task.initial_delay > 0:
                    task.next_run = datetime.now() + timedelta(seconds=task.initial_delay)
                else:
                    task.next_run = datetime.now()
            
            # Main loop
            while not self.stop_requested:
                now = datetime.now()
                
                # Check each task
                for name, task in self.tasks.items():
                    # Skip if task is running
                    if task.running:
                        continue
                    
                    # Skip if task is not due
                    if task.next_run and task.next_run > now:
                        continue
                    
                    # Execute task
                    task.task = asyncio.create_task(task.execute())
                
                # Sleep briefly
                await asyncio.sleep(1)
        
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")
        
        except Exception as e:
            logger.error(f"Error in scheduler loop: {str(e)}")
            self.running = False
    
    def get_task_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all tasks.
        
        Returns:
            Dictionary with task status
        """
        status = {}
        
        for name, task in self.tasks.items():
            status[name] = {
                "running": task.running,
                "last_run": task.last_run.isoformat() if task.last_run else None,
                "next_run": task.next_run.isoformat() if task.next_run else None,
                "failures": task.failures,
                "interval": task.interval
            }
        
        return status
    
    def setup_default_tasks(self) -> None:
        """Setup default tasks for the crawler."""
        # Add metrics update task
        self.add_task(Task(
            name="update_metrics",
            coroutine=self.controller.update_metrics,
            interval=60,
            initial_delay=10
        ))
        
        # Add failed summoners reset task
        self.add_task(Task(
            name="reset_failed",
            coroutine=self.controller.reset_failed,
            interval=3600,  # 1 hour
            initial_delay=1800  # 30 minutes
        ))


# For direct execution
async def main():
    """Run the crawler scheduler."""
    # Create controller
    controller = CrawlerController()
    
    # Add seed summoners
    seeds = [
        ("Faker", "kr"),
        ("Hide on bush", "kr"),
        ("Bjergsen", "na1"),
        ("Rekkles", "euw1")
    ]
    
    await controller.add_seed_summoners(seeds)
    
    # Create scheduler
    scheduler = CrawlerScheduler(controller)
    scheduler.setup_default_tasks()
    
    # Start scheduler
    await scheduler.start()
    
    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(60)
            # Print task status
            status = scheduler.get_task_status()
            print(f"Task status: {status}")
    
    except KeyboardInterrupt:
        print("Stopping scheduler...")
    
    finally:
        await scheduler.stop()


if __name__ == "__main__":
    asyncio.run(main())