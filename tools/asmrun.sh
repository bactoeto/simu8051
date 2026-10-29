#!/bin/bash
set -e

if [ $# -lt 1 ]; then
    echo "usage: ${0##*/} FILE.asm [simulator options]" >&2
    exit 2
fi

no_ext="${1%.asm}"
shift

rm -f "$no_ext.lst" "$no_ext.hex"
as31 "$no_ext.asm"
simu8051 "$no_ext.hex" "$@"
