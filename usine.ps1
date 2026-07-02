# Wrapper : .\usine.ps1 run --count 2 --parallel 2  |  .\usine.ps1 report  |  etc.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
docker compose run --rm usine @Args
