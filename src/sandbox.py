"""
KorgKode Sandbox Manager — Enterprise Cloud Virtualization.

Modes:
1. Docker (local) — fast, free, full isolation
2. Modal (cloud) — serverless, pay-per-use, GCP-backed
3. Direct (fallback) — local execution, no isolation

Usage:
    from src.sandbox import SandboxManager
    sb = SandboxManager.get("docker")
    result = sb.run("python -m pytest tests/")
"""

import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional


class SandboxBase:
    """Base sandbox interface."""
    
    def run(self, command: str, timeout: int = 300) -> dict:
        raise NotImplementedError
    
    def setup(self, repo_root: str) -> dict:
        raise NotImplementedError
    
    def cleanup(self):
        raise NotImplementedError


class DockerSandbox(SandboxBase):
    """Local Docker sandbox with full isolation."""
    
    IMAGE_NAME = "korgkode-sandbox:latest"
    
    def __init__(self):
        self.container_id = None
        self.workspace = "/workspace"
        self._ensure_image()
    
    def _ensure_image(self):
        """Build the Docker image if not present."""
        dockerfile = Path(__file__).parent.parent / "sandbox" / "Dockerfile"
        if not dockerfile.exists():
            return
        result = subprocess.run(
            ["docker", "images", "-q", self.IMAGE_NAME],
            capture_output=True, text=True, timeout=30
        )
        if not result.stdout.strip():
            print("Building KorgKode sandbox image...")
            subprocess.run(
                ["docker", "build", "-t", self.IMAGE_NAME, "-f", str(dockerfile), str(dockerfile.parent)],
                timeout=300
            )
    
    def setup(self, repo_root: str) -> dict:
        """Clone repo into sandbox and start container."""
        repo_root = os.path.abspath(repo_root)
        
        result = subprocess.run(
            ["docker", "run", "-d",
             "-v", f"{repo_root}:{self.workspace}",
             "-w", self.workspace,
             self.IMAGE_NAME,
             "tail", "-f", "/dev/null"],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode != 0:
            return {"error": f"Docker run failed: {result.stderr}"}
        
        self.container_id = result.stdout.strip()
        return {"container_id": self.container_id, "workspace": self.workspace}
    
    def run(self, command: str, timeout: int = 300) -> dict:
        """Run a command inside the sandbox container."""
        if not self.container_id:
            return {"error": "Sandbox not started. Call setup() first."}
        
        result = subprocess.run(
            ["docker", "exec", self.container_id,
             "bash", "-c", command],
            capture_output=True, text=True, timeout=timeout
        )
        
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    
    def cleanup(self):
        """Stop and remove the sandbox container."""
        if self.container_id:
            subprocess.run(["docker", "rm", "-f", self.container_id],
                         capture_output=True, timeout=10)
            self.container_id = None


try:
    import modal
    MODAL_AVAILABLE = True
except ImportError:
    MODAL_AVAILABLE = False


class ModalSandbox(SandboxBase):
    """Cloud sandbox via Modal — serverless, GCP-backed, enterprise."""
    
    def __init__(self):
        self.app = None
        self.function = None
        self._setup_modal()
    
    def _setup_modal(self):
        if not MODAL_AVAILABLE:
            return
        
        self.app = modal.App("korgkode-sandbox")
        
        image = modal.Image.debian_slim(python_version="3.12").pip_install(
            "pytest", "ruff", "black", "mypy", "requests"
        ).apt_install(
            "git", "curl", "jq", "ripgrep", "nodejs", "npm"
        )
        
        @self.app.function(image=image, timeout=600)
        def run_in_cloud(cmd: str, repo_url: str = None):
            import subprocess, os, tempfile
            
            results = {"stdout": "", "stderr": "", "exit_code": 0}
            
            if repo_url:
                repo_name = repo_url.split("/")[-1].replace(".git", "")
                os.chdir(tempfile.mkdtemp())
                subprocess.run(["git", "clone", repo_url], capture_output=True, timeout=120)
                os.chdir(repo_name)
            
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=500
            )
            
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        
        self.function = run_in_cloud
    
    def setup(self, repo_root: str) -> dict:
        return {"mode": "modal", "status": "ready"}
    
    def run(self, command: str, timeout: int = 300) -> dict:
        if not self.function:
            return {"error": "Modal not configured. Install modal: pip install modal"}
        return self.function.remote(cmd=command)
    
    def cleanup(self):
        pass


class DirectSandbox(SandboxBase):
    """Direct execution — no isolation. Used as fallback."""
    
    def __init__(self):
        self.cwd = os.getcwd()
    
    def setup(self, repo_root: str) -> dict:
        self.cwd = repo_root
        return {"mode": "direct", "cwd": self.cwd}
    
    def run(self, command: str, timeout: int = 300) -> dict:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True, text=True,
                timeout=timeout, cwd=self.cwd
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Command timed out", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}
    
    def cleanup(self):
        pass


class SandboxManager:
    """Factory for creating sandbox instances."""
    
    _instances = {}
    
    @classmethod
    def get(cls, mode: str = None) -> SandboxBase:
        """Get or create a sandbox.
        
        Modes (in order of preference):
        - "modal" — Modal cloud (enterprise, serverless)
        - "docker" — Docker local (full isolation)
        - "direct" — No isolation (fallback)
        - None — auto-detect (modal > docker > direct)
        """
        if mode is None:
            mode = os.environ.get("KORGKODE_SANDBOX", "auto")
        
        if mode == "auto":
            if MODAL_AVAILABLE:
                mode = "modal"
            elif shutil.which("docker"):
                mode = "docker"
            else:
                mode = "direct"
        
        if mode not in cls._instances:
            if mode == "modal":
                cls._instances[mode] = ModalSandbox()
            elif mode == "docker":
                cls._instances[mode] = DockerSandbox()
            else:
                cls._instances[mode] = DirectSandbox()
        
        return cls._instances[mode]