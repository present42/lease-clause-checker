# Records demo/check.gif: `vhs demo/check.tape`, run from the repository root.
# It replays the sample lease's saved answers and checks one saved closed-book quote, so it makes
# no API calls. The shell starts without the user's profile and with a bare "$ " prompt, so no
# username or path is recorded.
Output demo/check.gif

Set Shell "bash"
Set FontSize 15
Set Width 1000
Set Height 640
Set Padding 20
Set TypingSpeed 30ms
Set Theme "Builtin Dark"

Hide
Type "exec env -i PATH=$PATH TERM=xterm-256color LANG=en_US.UTF-8 BASH_SILENCE_DEPRECATION_WARNING=1 bash --noprofile --norc" Enter
Sleep 1s
Type "PS1='$ '; uv run --locked lcc --help > /dev/null; clear" Enter
Sleep 3s
Show

# With the statute in the prompt: every quote passes, and two (clauses 3 and 5) are flagged as
# possibly stopping before the list item that decides the clause. A pager shows them a page at
# a time.
Type "uv run lcc check samples/lease_short.txt --color always | less -R" Sleep 400ms Enter
Sleep 5s
Space
Sleep 5s
Hide
Type "q"
Type "clear" Enter
Sleep 300ms
Show

# Without it: Claude Opus 5.5's closed-book quote for clause c05, which fails.
Type@12ms 'uv run lcc quote check 15 "all or part of the remaining rent for a term or tenancy, or a specific sum, becomes due and payable if the tenant defaults in the payment of rent due"'
Sleep 400ms
Enter
Sleep 9s
