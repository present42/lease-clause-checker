# Records demo/check.gif: `vhs demo/check.tape`, run from the repository root.
# It replays the sample lease's saved answers, so it makes no API calls. The shell starts
# without the user's profile and with a bare "$ " prompt, so no username or path is recorded.
Output demo/check.gif

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 820
Set Padding 20
Set TypingSpeed 40ms
Set Theme "Builtin Dark"

Hide
Type "exec env -i PATH=$PATH TERM=xterm-256color BASH_SILENCE_DEPRECATION_WARNING=1 bash --noprofile --norc" Enter
Sleep 1s
Type "PS1='$ '; uv run --locked lcc --help > /dev/null; clear" Enter
Sleep 3s
Show

Type "uv run lcc check samples/lease_short.txt" Sleep 500ms Enter
Sleep 9s
