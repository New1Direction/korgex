import subprocess


def test_version_flag():
    # Run the korgex CLI with --version flag
    result = subprocess.run(['python3', 'src/cli.py', '--version'], capture_output=True, text=True)
    
    # Assert the command ran successfully
    assert result.returncode == 0
    
    # Assert the output is non-empty and looks like a version
    version_text = result.stdout.strip()
    assert version_text, "Expected a version string, got an empty output"
    assert any(char.isdigit() for char in version_text), "Expected digits in the version string"
    
    # Run the korgex CLI with -V flag
    result = subprocess.run(['python3', 'src/cli.py', '-V'], capture_output=True, text=True)
    
    # Assert the command ran successfully
    assert result.returncode == 0
    
    # Assert the output is non-empty and looks like a version
    version_text = result.stdout.strip()
    assert version_text, "Expected a version string, got an empty output"
    assert any(char.isdigit() for char in version_text), "Expected digits in the version string"
