"""AgentSwarm.run_graph — run swarm subtasks as a dependency DAG (exec_graph).

This is the bridge that turns the swarm's flat parallel fan-out into an ordered
graph: nodes carry SubTasks, deps order them, independent nodes still parallelize,
and a failed subtask blocks its dependents. A fake agent_factory keeps it offline.
"""
import threading

from src.exec_graph import Node
from src.swarm import AgentSwarm, SubTask


def test_run_graph_runs_dependencies_before_dependents():
    order, lock = [], threading.Lock()

    class FakeAgent:
        def __init__(self, task):
            self.task = task

        def run_task(self, prompt):
            with lock:
                order.append(self.task.agent_type)
            return {"success": True, "result": f"did {self.task.agent_type}"}

    swarm = AgentSwarm(max_parallel=3, agent_factory=lambda task: FakeAgent(task))
    nodes = [
        Node("build", SubTask("test", "build it", "/repo")),
        Node("ship", SubTask("docs", "ship it", "/repo"), deps=["build"]),
    ]
    out = swarm.run_graph(nodes)

    assert order.index("test") < order.index("docs")     # build ran before ship
    assert out["completed"] == ["build", "ship"]
    assert out["results"]["build"].success is True


def test_run_graph_skips_dependents_of_a_failed_subtask():
    class FakeAgent:
        def __init__(self, task):
            self.task = task

        def run_task(self, prompt):
            ok = self.task.agent_type != "security"      # the security scan fails
            return {"success": ok, "result": "boom" if not ok else "ok"}

    swarm = AgentSwarm(agent_factory=lambda task: FakeAgent(task))
    nodes = [
        Node("scan", SubTask("security", "scan", "/repo")),
        Node("fix", SubTask("refactor", "fix", "/repo"), deps=["scan"]),
        Node("indep", SubTask("docs", "unrelated", "/repo")),
    ]
    out = swarm.run_graph(nodes)

    assert "scan" in out["failed"]
    assert "fix" in out["skipped"]
    assert "indep" in out["completed"]    # independent node still runs
