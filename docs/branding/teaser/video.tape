# Video teaser — plays a local clip through tplay (video → ASCII), captured to
# raw-video.mp4. crt.sh then adds the CRT pass.
#
#   yt-dlp --download-sections "*0:03-0:10.5" -f bv*[height<=720] --recode mp4 -o clip.mp4 <URL>
#   vhs video.tape && bash crt.sh raw-video.mp4

Output raw-video.mp4

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 14
Set Width 1280
Set Height 720
Set Padding 0
Set Theme '{ "name": "blk", "background": "#000000", "foreground": "#FFFFFF", "black": "#000000", "red": "#FF5555", "green": "#55FF55", "yellow": "#FFFF55", "blue": "#5555FF", "magenta": "#FF55FF", "cyan": "#55FFFF", "white": "#FFFFFF", "brightBlack": "#555555", "brightRed": "#FF7777", "brightGreen": "#77FF77", "brightYellow": "#FFFF77", "brightBlue": "#7777FF", "brightMagenta": "#FF77FF", "brightCyan": "#77FFFF", "brightWhite": "#FFFFFF" }'

Hide
Type "export PATH=$HOME/.cargo/bin:/opt/homebrew/bin:$PATH"
Enter
Type "clear"
Enter
Show

Sleep 400ms
Type "tplay clip.mp4 -f 24"
Sleep 200ms
Enter
Sleep 12000ms
