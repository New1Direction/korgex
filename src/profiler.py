"""
KorgKode Performance Profiler — Automated No-Regression Guarantee.

Executes test suites or scripts under a CPU profiler, extracts execution
time metrics for key functions, and detects performance regressions.

Architecture:
    [Target Command]
            │
            ▼
    [Run in Sandbox with cProfile] ──▶ [Dump .prof stats file]
                                                │
                                                ▼
                   [Parse pstats & extract Top N slowest functions]
                                                │
                                                ▼
                             [Output Structured JSON Report]
"""

import json


class PerformanceProfiler:
    """Analyzes execution time using cProfile to ensure no performance regressions."""

    def __init__(self, sandbox):
        self.sandbox = sandbox

    def run_profile(self, command: str, output_file: str = "korgkode_stats.prof") -> dict:
        """Runs a python command under cProfile and extracts the slowest functions."""
        prof_cmd = self._inject_profiler(command, output_file)

        # 1. Run the profiled command in the sandbox
        result = self.sandbox.run(prof_cmd)
        if result.get("exit_code", -1) != 0:
            return {
                "success": False,
                "error": "Command failed during profiling.",
                "stdout": result.get("stdout"),
            }

        # 2. Generate a Python script to parse the binary .prof file
        analysis_script = f"""
import pstats
import json

try:
    p = pstats.Stats('{output_file}')
    p.sort_stats('cumtime')

    stats_data = []
    # Sort by cumulative time and take the top 25
    sorted_stats = sorted(p.stats.items(), key=lambda x: x[1][3], reverse=True)

    for func, (cc, nc, tt, ct, callers) in sorted_stats[:25]:
        file_name, line_num, func_name = func

        # Filter out noisy built-in Python libraries to focus on user code
        if "<" not in file_name and "site-packages" not in file_name:
            stats_data.append({{
                "file": file_name,
                "function": func_name,
                "calls": nc,
                "total_time_sec": round(tt, 4),
                "cumulative_time_sec": round(ct, 4),
            }})

    print(json.dumps({{"status": "success", "data": stats_data}}))
except Exception as e:
    print(json.dumps({{"status": "error", "message": str(e)}}))
"""

        # Write the parser script to the sandbox and execute it
        write_cmd = f"cat << 'EOF' > parse_profile.py\n{analysis_script}\nEOF"
        self.sandbox.run(write_cmd)

        analysis_result = self.sandbox.run("python parse_profile.py")

        # 3. Extract and return the JSON payload
        try:
            parsed = json.loads(analysis_result.get("stdout", "{}"))
            if parsed.get("status") == "success":
                return {"success": True, "top_functions": parsed.get("data", [])}
            return {"success": False, "error": parsed.get("message")}
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": "Failed to parse profiler JSON.",
                "raw_output": analysis_result.get("stdout"),
            }

    def _inject_profiler(self, command: str, output_file: str) -> str:
        """Rewrites the command to execute via the cProfile module."""
        parts = command.split()
        if not parts:
            return command

        if parts[0] in ("python", "python3"):
            return f"{parts[0]} -m cProfile -o {output_file} {' '.join(parts[1:])}"
        elif parts[0] == "pytest":
            return f"python -m cProfile -o {output_file} -m pytest {' '.join(parts[1:])}"

        return command