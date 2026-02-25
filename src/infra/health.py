"""
Health Check HTTP Endpoint
Simple HTTP server for container orchestration health checks.
Provides /health, /ready, and /metrics endpoints.
"""
import json
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
import logging

logger = logging.getLogger('jamf-snipeit-health')


@dataclass
class HealthStatus:
    """Health status data."""
    status: str = "healthy"  # healthy, degraded, unhealthy
    version: str = "1.0.0"
    uptime_seconds: float = 0
    start_time: str = ""
    last_check: str = ""
    
    # Component health
    jamf_healthy: bool = True
    snipe_healthy: bool = True
    azure_healthy: bool = True
    scheduler_running: bool = False
    
    # Metrics
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    last_run_status: str = "none"
    last_run_time: str = ""
    
    # Custom checks
    custom_checks: Dict[str, bool] = field(default_factory=dict)


class HealthCheckServer:
    """
    HTTP server providing health check endpoints for container orchestration.
    
    Endpoints:
        /health  - Liveness probe (is the service running?)
        /ready   - Readiness probe (is the service ready to accept traffic?)
        /metrics - Prometheus-style metrics
        /status  - Detailed status JSON
    """
    
    def __init__(self, port: int = 8080, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self.status = HealthStatus()
        self.status.start_time = datetime.now().isoformat()
        self._start_time = time.time()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._custom_health_checks: Dict[str, Callable[[], bool]] = {}
    
    def register_health_check(self, name: str, check_fn: Callable[[], bool]):
        """
        Register a custom health check function.
        
        Args:
            name: Name of the health check
            check_fn: Function that returns True if healthy, False otherwise
        """
        self._custom_health_checks[name] = check_fn
    
    def update_status(self, **kwargs):
        """Update health status fields."""
        for key, value in kwargs.items():
            if hasattr(self.status, key):
                setattr(self.status, key, value)
    
    def record_run(self, success: bool, module_name: str = ""):
        """Record a module run for metrics."""
        self.status.total_runs += 1
        if success:
            self.status.successful_runs += 1
            self.status.last_run_status = f"success:{module_name}"
        else:
            self.status.failed_runs += 1
            self.status.last_run_status = f"failed:{module_name}"
        self.status.last_run_time = datetime.now().isoformat()
    
    def _run_custom_checks(self) -> Dict[str, bool]:
        """Run all registered custom health checks."""
        results = {}
        for name, check_fn in self._custom_health_checks.items():
            try:
                results[name] = check_fn()
            except Exception as e:
                logger.warning(f"Health check '{name}' failed with error: {e}")
                results[name] = False
        return results
    
    def _get_status_dict(self) -> Dict:
        """Get current status as dictionary."""
        self.status.uptime_seconds = time.time() - self._start_time
        self.status.last_check = datetime.now().isoformat()
        self.status.custom_checks = self._run_custom_checks()
        
        # Determine overall status
        all_healthy = (
            self.status.jamf_healthy and 
            self.status.snipe_healthy and
            all(self.status.custom_checks.values()) if self.status.custom_checks else True
        )
        
        if all_healthy:
            self.status.status = "healthy"
        elif self.status.failed_runs > self.status.successful_runs:
            self.status.status = "unhealthy"
        else:
            self.status.status = "degraded"
        
        return {
            'status': self.status.status,
            'version': self.status.version,
            'uptime_seconds': round(self.status.uptime_seconds, 2),
            'start_time': self.status.start_time,
            'last_check': self.status.last_check,
            'components': {
                'jamf': 'healthy' if self.status.jamf_healthy else 'unhealthy',
                'snipeit': 'healthy' if self.status.snipe_healthy else 'unhealthy',
                'azure': 'healthy' if self.status.azure_healthy else 'unhealthy',
                'scheduler': 'running' if self.status.scheduler_running else 'stopped'
            },
            'metrics': {
                'total_runs': self.status.total_runs,
                'successful_runs': self.status.successful_runs,
                'failed_runs': self.status.failed_runs,
                'last_run_status': self.status.last_run_status,
                'last_run_time': self.status.last_run_time
            },
            'custom_checks': self.status.custom_checks
        }
    
    def _get_metrics_prometheus(self) -> str:
        """Get metrics in Prometheus format."""
        self.status.uptime_seconds = time.time() - self._start_time
        
        lines = [
            "# HELP jamf_snipeit_up Service is up and running",
            "# TYPE jamf_snipeit_up gauge",
            f"jamf_snipeit_up 1",
            "",
            "# HELP jamf_snipeit_uptime_seconds Time since service started",
            "# TYPE jamf_snipeit_uptime_seconds counter",
            f"jamf_snipeit_uptime_seconds {self.status.uptime_seconds:.2f}",
            "",
            "# HELP jamf_snipeit_runs_total Total number of module runs",
            "# TYPE jamf_snipeit_runs_total counter",
            f'jamf_snipeit_runs_total{{result="success"}} {self.status.successful_runs}',
            f'jamf_snipeit_runs_total{{result="failed"}} {self.status.failed_runs}',
            "",
            "# HELP jamf_snipeit_component_healthy Component health status",
            "# TYPE jamf_snipeit_component_healthy gauge",
            f'jamf_snipeit_component_healthy{{component="jamf"}} {1 if self.status.jamf_healthy else 0}',
            f'jamf_snipeit_component_healthy{{component="snipeit"}} {1 if self.status.snipe_healthy else 0}',
            f'jamf_snipeit_component_healthy{{component="azure"}} {1 if self.status.azure_healthy else 0}',
            f'jamf_snipeit_component_healthy{{component="scheduler"}} {1 if self.status.scheduler_running else 0}',
        ]
        
        # Add custom checks
        for name, healthy in self.status.custom_checks.items():
            lines.append(f'jamf_snipeit_component_healthy{{component="{name}"}} {1 if healthy else 0}')
        
        return "\n".join(lines) + "\n"
    
    def _create_handler(self):
        """Create HTTP request handler with access to health server."""
        health_server = self
        
        class HealthHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Suppress default logging
                pass
            
            def _send_response(self, status_code: int, content_type: str, body: str):
                self.send_response(status_code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode('utf-8'))
            
            def do_GET(self):
                if self.path == '/health' or self.path == '/healthz':
                    # Liveness probe - is the service running?
                    status = health_server._get_status_dict()
                    is_alive = status['status'] != 'unhealthy'
                    
                    response = {'status': 'ok' if is_alive else 'error'}
                    status_code = 200 if is_alive else 503
                    
                    self._send_response(status_code, 'application/json', 
                                       json.dumps(response))
                
                elif self.path == '/ready' or self.path == '/readyz':
                    # Readiness probe - is the service ready?
                    status = health_server._get_status_dict()
                    is_ready = status['status'] == 'healthy'
                    
                    response = {
                        'ready': is_ready,
                        'components': status['components']
                    }
                    status_code = 200 if is_ready else 503
                    
                    self._send_response(status_code, 'application/json',
                                       json.dumps(response))
                
                elif self.path == '/metrics':
                    # Prometheus metrics
                    metrics = health_server._get_metrics_prometheus()
                    self._send_response(200, 'text/plain; charset=utf-8', metrics)
                
                elif self.path == '/status':
                    # Detailed status
                    status = health_server._get_status_dict()
                    self._send_response(200, 'application/json',
                                       json.dumps(status, indent=2))
                
                else:
                    # Root or unknown path - return basic info
                    response = {
                        'service': 'Jamf-SnipeIT Suite',
                        'version': health_server.status.version,
                        'endpoints': ['/health', '/ready', '/metrics', '/status']
                    }
                    self._send_response(200, 'application/json',
                                       json.dumps(response, indent=2))
        
        return HealthHandler
    
    def start(self, blocking: bool = False):
        """
        Start the health check HTTP server.
        
        Args:
            blocking: If True, block the current thread. If False, run in background.
        """
        handler = self._create_handler()
        self._server = HTTPServer((self.host, self.port), handler)
        
        logger.info(f"Health server listening on {self.host}:{self.port}")
        
        if blocking:
            self._server.serve_forever()
        else:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
    
    def stop(self):
        """Stop the health check server."""
        if self._server:
            logger.info("Stopping health check server...")
            self._server.shutdown()
            self._server = None
            self._thread = None


# Global health server instance for easy access
_health_server: Optional[HealthCheckServer] = None


def get_health_server() -> Optional[HealthCheckServer]:
    """Get the global health server instance."""
    return _health_server


def start_health_server(port: int = 8080, host: str = "0.0.0.0") -> HealthCheckServer:
    """
    Start the global health check server.
    
    Args:
        port: Port to listen on (default: 8080)
        host: Host to bind to (default: 0.0.0.0)
    
    Returns:
        HealthCheckServer instance
    """
    global _health_server
    
    if _health_server is not None:
        logger.warning("Health server already running")
        return _health_server
    
    _health_server = HealthCheckServer(port=port, host=host)
    _health_server.start(blocking=False)
    
    return _health_server


def stop_health_server():
    """Stop the global health check server."""
    global _health_server
    
    if _health_server:
        _health_server.stop()
        _health_server = None


if __name__ == "__main__":
    # Test the health server standalone
    import signal
    import sys
    
    def signal_handler(sig, frame):
        print("\nShutting down...")
        stop_health_server()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    server = start_health_server(port=8080)
    
    # Simulate some activity
    print("\nSimulating module runs...")
    time.sleep(2)
    server.record_run(success=True, module_name="leavers")
    time.sleep(2)
    server.record_run(success=True, module_name="user_match")
    time.sleep(2)
    server.record_run(success=False, module_name="model_sync")
    
    print("\nHealth server running. Press Ctrl+C to stop.")
    print(f"Test with: curl http://localhost:8080/status")
    
    # Keep running
    while True:
        time.sleep(1)
