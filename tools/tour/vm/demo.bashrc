# The shell of the terminal clips: a neutral prompt, ~/.local/bin on PATH, BROWSER = the recorded
# Chromium, and a stamp per prompt that rec.mjs's run() waits for.
export PATH="$HOME/.local/bin:$PATH"
export BROWSER="$HOME/lab/video/open-in-cdp.sh"
PS1="\[\e[1;32m\]demo@lab\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\$ "
PROMPT_COMMAND="date +%s.%N > /tmp/demo-prompt"
mkdir -p ~/lab/demo && cd ~/lab/demo
clear
