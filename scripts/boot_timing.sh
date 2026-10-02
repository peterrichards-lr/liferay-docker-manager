#!/usr/bin/env bash
#
# Deterministic Liferay boot timing -- compares LDM versions on ONE machine.
#
# Written for LDM-#2050, to answer "did this release make Liferay slower?"
# with numbers instead of impressions. It is deliberately a measuring
# instrument and not a gate: it asserts nothing and fails nothing.
#
# It reports two quantities per run, which are NOT interchangeable:
#
#   tomcat_ms  Liferay's OWN figure, lifted from its log. Independent of
#              Docker, of LDM, and of this script's polling granularity.
#              Use it to answer "is the application slower?".
#
#   healthy_s  Container start -> Docker reports healthy. This is what
#              `depends_on: condition: service_healthy` actually gates on,
#              so it is the number that decides whether dependent
#              containers start. Use it to answer "is the STACK slower?".
#
# The second can move without the first: it is a function of the
# healthcheck's interval, retries and start_period, which LDM writes
# (ldm_core/handlers/composer.py). A change there moves healthy_s and
# leaves tomcat_ms alone. That is a real difference, not noise, and
# separating the two is the point of reporting both.
#
# `ldm run --no-wait` is used deliberately: it takes LDM's own readiness
# polling out of the measurement path, so healthy_s is Docker's verdict
# rather than `ldm wait`'s.
#
# PROTOCOL -- the numbers mean nothing without it:
#
#   * One machine. Never compare a figure from one host to another.
#   * Image pre-pulled (this script does it, outside the timed window).
#   * A FRESH project per run, deleted afterwards, so no OSGi state,
#     no database and no volume carries into the next run.
#   * Several runs. A single run cannot distinguish a regression from
#     variance -- the observation that prompted this script was two runs
#     of the SAME build differing by 84 seconds, 24%.
#   * A quiesced machine. This script cannot control background load and
#     does not pretend to; it is on you not to run a build alongside it.
#
# Compare MEDIANS, and compare the ranges. If the ranges overlap, you have
# not measured a difference.
#
# Usage:
#   scripts/boot_timing.sh [--tag TAG] [--runs N] [--timeout SECONDS] [--keep]
#
# Example -- the A/B that refuted a suspected v2.26.0 startup regression:
#   ldm system version --set 2.25.0   # or install the older binary
#   scripts/boot_timing.sh --runs 3 | tee /tmp/bt-2.25.0.txt
#   # upgrade, then:
#   scripts/boot_timing.sh --runs 3 | tee /tmp/bt-2.26.2.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/colors.sh" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/colors.sh"
else
    UI_COLOR_OFF=''
    UI_BGREEN=''
    UI_BYELLOW=''
    UI_BRED=''
    UI_BCYAN=''
fi

info() { echo -e "${UI_BCYAN}i  $1${UI_COLOR_OFF}"; }
success() { echo -e "${UI_BGREEN}OK $1${UI_COLOR_OFF}"; }
warning() { echo -e "${UI_BYELLOW}!  $1${UI_COLOR_OFF}"; }
error() { echo -e "${UI_BRED}X  $1${UI_COLOR_OFF}" >&2; }

TAG="2026.q3.5"
RUNS=3
TIMEOUT=900
POLL_INTERVAL=2
KEEP=0
LDM_CMD="${LDM_CMD:-ldm}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            TAG="$2"
            shift 2
            ;;
        --runs)
            RUNS="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --keep)
            KEEP=1
            shift
            ;;
        -h | --help)
            sed -n '2,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# --- helpers -------------------------------------------------------------

# Docker emits RFC3339 with nanoseconds (2026-10-02T09:12:33.123456789Z).
# GNU date and BSD date disagree about how to read that, and python3 reads
# it on both. Try each, and FAIL rather than substitute an approximation:
# an earlier draft of this script fell back to "the time we started the
# command" when `date -d` was unavailable, which silently measured a
# different quantity on macOS than on Linux and still printed a number.
iso_to_epoch() {
    local iso="$1" trimmed
    trimmed="${iso%%.*}"
    trimmed="${trimmed%Z}"

    if date -u -d "${trimmed}Z" +%s 2>/dev/null; then return 0; fi
    if date -u -j -f '%Y-%m-%dT%H:%M:%S' "$trimmed" +%s 2>/dev/null; then return 0; fi
    if command -v python3 >/dev/null 2>&1 &&
        python3 -c 'import datetime,sys; print(int(datetime.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()))' "$trimmed" 2>/dev/null; then
        return 0
    fi

    error "Cannot parse the Docker timestamp '$iso' with date(1) or python3."
    return 1
}

now_epoch() { date -u +%s; }

# min / median / max over the numeric arguments. Returns "NA NA NA" when
# nothing measurable was collected, so a partly-failed sweep still prints
# a summary instead of dying under `set -e`.
summarise() {
    local -a vals=()
    local v
    for v in "$@"; do
        [[ "$v" =~ ^[0-9]+$ ]] && vals+=("$v")
    done
    if [ "${#vals[@]}" -eq 0 ]; then
        echo "NA NA NA"
        return 0
    fi
    local sorted
    sorted="$(printf '%s\n' "${vals[@]}" | sort -n)"
    local count=${#vals[@]}
    local mid=$((count / 2))
    local median
    median="$(echo "$sorted" | sed -n "$((mid + 1))p")"
    echo "$(echo "$sorted" | head -1) $median $(echo "$sorted" | tail -1)"
}

CURRENT_PROJECT=""
cleanup() {
    if [ -n "$CURRENT_PROJECT" ] && [ "$KEEP" -eq 0 ]; then
        warning "Interrupted -- tearing down $CURRENT_PROJECT"
        "$LDM_CMD" rm "$CURRENT_PROJECT" --delete -y >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

# --- pre-flight ----------------------------------------------------------

if ! command -v "$LDM_CMD" >/dev/null 2>&1; then
    error "'$LDM_CMD' is not on PATH. Set LDM_CMD to the binary you want to measure."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    error "Docker daemon is not running."
    exit 1
fi

LDM_VERSION="$("$LDM_CMD" --version 2>/dev/null || echo 'unknown')"

info "Pre-pulling liferay/dxp:$TAG (outside the timed window)"
docker pull "liferay/dxp:$TAG" >/dev/null 2>&1 || true
if ! docker image inspect "liferay/dxp:$TAG" >/dev/null 2>&1; then
    error "liferay/dxp:$TAG is not present locally and could not be pulled."
    error "A pull inside the timed window would be measured as boot time."
    exit 1
fi

RUNNING="$(docker ps --format '{{.Names}}' | wc -l | tr -d ' ')"
if [ "$RUNNING" -gt 0 ]; then
    warning "$RUNNING container(s) already running -- they compete for CPU and I/O."
    warning "Boot timings taken on a busy machine are not comparable with quiet ones."
fi

echo
echo "ldm:     $LDM_VERSION"
echo "tag:     $TAG"
echo "runs:    $RUNS"
echo "host:    $(uname -s) $(uname -r) ($(uname -m))"
echo "docker:  $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
echo

# --- measure -------------------------------------------------------------

declare -a TOMCAT_MS=()
declare -a HEALTHY_S=()

for i in $(seq 1 "$RUNS"); do
    PROJECT="bt-$(date +%s)-$i"
    CURRENT_PROJECT="$PROJECT"

    info "Run $i/$RUNS: $PROJECT"

    # --no-wait keeps LDM's readiness polling out of the measurement;
    # -c pins the container name so the probes below are not guessing at
    # how the project name was sanitised.
    if ! "$LDM_CMD" run "$PROJECT" -c "$PROJECT" -t "$TAG" --no-wait -y >/dev/null 2>&1; then
        error "  'ldm run' failed for $PROJECT -- skipping this run"
        "$LDM_CMD" rm "$PROJECT" --delete -y >/dev/null 2>&1 || true
        CURRENT_PROJECT=""
        continue
    fi

    started_at="$(docker inspect "$PROJECT" --format '{{.State.StartedAt}}' 2>/dev/null || true)"
    if [ -z "$started_at" ]; then
        error "  No container named '$PROJECT' after a successful 'ldm run'."
        error "  (probe: docker inspect $PROJECT --format '{{.State.StartedAt}}')"
        "$LDM_CMD" rm "$PROJECT" --delete -y >/dev/null 2>&1 || true
        CURRENT_PROJECT=""
        continue
    fi
    started_epoch="$(iso_to_epoch "$started_at")"

    healthy_s="NA"
    deadline=$(($(now_epoch) + TIMEOUT))
    while [ "$(now_epoch)" -lt "$deadline" ]; do
        status="$(docker inspect "$PROJECT" \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            2>/dev/null || echo gone)"
        case "$status" in
            healthy)
                healthy_s=$(($(now_epoch) - started_epoch))
                break
                ;;
            none)
                # No healthcheck at all. Report it rather than spending the
                # whole timeout discovering it: before LDM-#2050 this came
                # from the image, and a build that writes neither has no
                # healthy_s to measure.
                healthy_s="NOHC"
                break
                ;;
            gone)
                healthy_s="GONE"
                break
                ;;
        esac
        sleep "$POLL_INTERVAL"
    done
    [ "$healthy_s" = "NA" ] && healthy_s="TIMEOUT"

    # Tomcat's own figure. Both "Server startup in [138473] milliseconds"
    # and the older unbracketed form are matched; the stricter
    # 'Catalina.start Server startup in' anchor used by
    # verify_osgi_persistence.sh is avoided here because the logger prefix
    # varies across the Tomcat versions the supported DXP lines ship.
    tomcat_ms="$(docker logs "$PROJECT" 2>&1 |
        grep -oE 'Server startup in \[?[0-9]+' |
        grep -oE '[0-9]+' | tail -1 || true)"
    [ -z "$tomcat_ms" ] && tomcat_ms="NA"

    printf '   tomcat_ms=%-10s healthy_s=%s\n' "$tomcat_ms" "$healthy_s"

    TOMCAT_MS+=("$tomcat_ms")
    HEALTHY_S+=("$healthy_s")

    if [ "$KEEP" -eq 0 ]; then
        "$LDM_CMD" rm "$PROJECT" --delete -y >/dev/null 2>&1 || true
    else
        warning "  --keep: leaving $PROJECT in place"
    fi
    CURRENT_PROJECT=""
done

# --- report --------------------------------------------------------------

echo
read -r t_min t_med t_max <<<"$(summarise "${TOMCAT_MS[@]:-}")"
read -r h_min h_med h_max <<<"$(summarise "${HEALTHY_S[@]:-}")"

echo "----------------------------------------------------------"
printf '%-12s %10s %10s %10s\n' "metric" "min" "median" "max"
printf '%-12s %10s %10s %10s\n' "tomcat_ms" "$t_min" "$t_med" "$t_max"
printf '%-12s %10s %10s %10s\n' "healthy_s" "$h_min" "$h_med" "$h_max"
echo "----------------------------------------------------------"
echo
echo "Compare medians AND ranges against the other version. Overlapping"
echo "ranges mean no difference was measured, whatever the medians say."

if [ "$t_med" = "NA" ] && [ "$h_med" = "NA" ]; then
    error "No run produced a usable measurement."
    exit 1
fi

success "Done."
