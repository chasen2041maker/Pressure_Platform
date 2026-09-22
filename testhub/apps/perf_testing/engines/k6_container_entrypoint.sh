#!/bin/sh
# A sequence avoids clock differences between Windows and the Linux VM.
set -u
child=''
watchdog=''
cleanup() {
    trap '' TERM INT
    [ -z "$watchdog" ] || kill "$watchdog" 2>/dev/null || true
    [ -z "$child" ] || kill -TERM "$child" 2>/dev/null || true
}
trap 'cleanup; exit 143' TERM INT
/run/k6 run --address=127.0.0.1:6565 --quiet --log-format=raw --log-output=stdout /run/scenario.js &
child=$!
parent=$$
(
    previous=$(cat /run/heartbeat 2>/dev/null || true)
    unchanged=0
    while kill -0 "$child" 2>/dev/null; do
        sleep 0.5
        current=$(cat /run/heartbeat 2>/dev/null || true)
        if [ -n "$current" ] && [ "$current" != "$previous" ]; then
            unchanged=0
            previous=$current
        else
            unchanged=$((unchanged + 1))
        fi
        if [ "$unchanged" -ge 10 ]; then
            kill -TERM "$child" 2>/dev/null || true
            sleep 1
            kill -KILL "$child" 2>/dev/null || true
            kill -TERM "$parent" 2>/dev/null || true
            exit 1
        fi
    done
) &
watchdog=$!
wait "$child"
result=$?
cleanup
exit "$result"
