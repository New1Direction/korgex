# korgex demo recording — produces docs/images/demo.gif
#
#   brew install vhs           # https://github.com/charmbracelet/vhs
#   export ANTHROPIC_API_KEY=… # (or any provider key — this runs a REAL agent task)
#   cd docs/branding && vhs demo.tape
#
# Tune the prompt and the Sleep after it to match how long your run takes.
# Font: install Departure Mono (https://departuremono.com/) or swap FontFamily below.

Output ../images/demo.gif

Set Shell "bash"
Set FontFamily "Departure Mono"
Set FontSize 22
Set Width 1280
Set Height 720
Set Padding 30
Set Theme '{ "name": "korgex-y2k", "background": "#060B16", "foreground": "#D6ECFF", "cursor": "#2DE2FF", "selection": "#16335C", "black": "#0A1120", "red": "#FF5566", "green": "#4DFFA6", "yellow": "#FFD23F", "blue": "#2D9BFF", "magenta": "#B48CFF", "cyan": "#2DE2FF", "white": "#D6ECFF", "brightBlack": "#3A4A63", "brightRed": "#FF7B88", "brightGreen": "#8EFFC4", "brightYellow": "#FFE27A", "brightBlue": "#6CC0FF", "brightMagenta": "#CFB0FF", "brightCyan": "#7DEFFF", "brightWhite": "#F2FAFF" }'

Sleep 800ms
Type 'korgex "add a /healthz endpoint that returns 200 with uptime"'
Sleep 600ms
Enter
Sleep 10s
Type "korgex verify"
Sleep 400ms
Enter
Sleep 4s
