# Shared settings for the tools/*.sh scripts. Source it: . "$(dirname "$0")/config.sh"
# Every value can be set in the environment or in a config file (plain KEY=value shell lines):
#   ${EU4_MCP_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/eu4-mcp/config.env}
EU4_CONFIG_FILE="${EU4_MCP_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/eu4-mcp/config.env}"
# shellcheck disable=SC1090
[ -r "$EU4_CONFIG_FILE" ] && . "$EU4_CONFIG_FILE"
: "${EU4_GAME_DIR:=$HOME/games/eu4}"              # game install (contains the `eu4` binary)
: "${EU4_MCP_STATE:=$HOME/.config/eu4-mcp}"       # hook socket, X cookie, runtime state
: "${EU4_DISPLAY:=:99}"                           # X display the game runs on
: "${EU4_BIND:=127.0.0.1}"                        # address the watch page and the noVNC viewer listen on
: "${EU4_HOST:=$EU4_BIND}"                        # host name shown in printed links (set it when EU4_BIND is 0.0.0.0)
: "${EU4_VNC_PORT:=6080}"                         # noVNC game viewer
: "${EU4_WATCH_PORT:=6081}"                       # watch page
: "${EU4_DISCORD_PORT:=6082}"                     # noVNC of the optional Discord display
: "${EU4_DISCORD_DISPLAY:=:98}"                   # second X display for the optional Discord client
: "${EU4_XORG_CONF:=}"                            # xorg.conf for tools/display.sh (default: display/xorg.conf if present, else none)
: "${EU4_XORG_VT:=}"                              # e.g. vt8; empty = Xorg picks
: "${EU4_PLAYER_SESSION:=eu4-player}"             # tmux session name for the playing Claude
: "${EU4_PLAYER_CMD:=}"                           # command that starts the player (see README); empty = `eu4 play` prints how
: "${EU4_PLAYER_PROMPT:=}"                       # file with the player prompt (copy tools/player_prompt.example.txt)
export EU4_GAME_DIR EU4_MCP_STATE EU4_DISPLAY EU4_BIND EU4_HOST EU4_VNC_PORT EU4_WATCH_PORT EU4_MCP_CONFIG="$EU4_CONFIG_FILE"
EU4_XAUTH="$EU4_MCP_STATE/Xauthority"
