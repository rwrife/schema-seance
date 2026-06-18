# schema-seance VHS tape
#
# Renders an animated GIF demo of `seance` summoning a tiny CSV.
# Requires https://github.com/charmbracelet/vhs (>= 0.7) on $PATH.
#
# Render with:
#     vhs docs/demo/seance.tape
#
# Output: docs/demo/seance.gif (also embedded in README).

Output docs/demo/seance.gif

Set Shell      "bash"
Set FontSize   14
Set Width      1100
Set Height     700
Set Padding    20
Set Theme      "Dracula"
Set TypingSpeed 35ms
Set PlaybackSpeed 1.0

# Quiet the shell prompt so the demo reads cleanly.
Hide
Type "export PS1='$ '"   Enter
Type "clear"             Enter
Show

# 1. Greet Madame Schema.
Type "seance"            Sleep 400ms Enter
Sleep 2500ms

# 2. Summon a tiny fixture.
Type "seance summon tests/fixtures/tiny.csv"  Sleep 400ms Enter
Sleep 4500ms

# 3. Show the JSON contract on the same fixture.
Type "seance summon tests/fixtures/tiny.csv --json | jq '.columns[].name'"  Sleep 400ms Enter
Sleep 3500ms
