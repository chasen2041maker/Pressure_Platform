package main

import (
    _ "pressure.local/bounded-sse"
    "go.k6.io/k6/v2/cmd"
)

func main() { cmd.Execute() }
