# Launch-teaser recording — renders the chrome banner as terminal ANSI art,
# captured to raw.mp4. crt.sh then adds the CRT pass and exports gif + mp4.
#
#   brew install vhs chafa
#   cd docs/branding/teaser && vhs banner.tape && bash crt.sh
#
# Font: "Departure Mono" (https://departuremono.com) for brand parity; falls
# back to a system mono if not installed. The banner is image-rendered (chafa),
# so the font only affects any typed text.

Output raw.mp4

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 20
Set Width 1100
Set Height 620
Set Padding 26
Set TypingSpeed 38ms
Set Theme '{ "name": "korgex-y2k", "background": "#060B16", "foreground": "#D6ECFF", "cursor": "#2DE2FF", "selection": "#16335C", "black": "#0A1120", "red": "#FF5566", "green": "#4DFFA6", "yellow": "#FFD23F", "blue": "#2D9BFF", "magenta": "#B48CFF", "cyan": "#2DE2FF", "white": "#D6ECFF", "brightBlack": "#3A4A63", "brightRed": "#FF7B88", "brightGreen": "#8EFFC4", "brightYellow": "#FFE27A", "brightBlue": "#6CC0FF", "brightMagenta": "#CFB0FF", "brightCyan": "#7DEFFF", "brightWhite": "#F2FAFF" }'

Sleep 500ms
Type "chafa -c full -f symbols --size=88x24 ../../images/banner.jpg"
Sleep 300ms
Enter
Sleep 3400ms
