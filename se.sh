curl -X POST https://monitoringapi.solaredge.com/v2/oauth2/token
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "authorization_code",
    "code": "lOSQaELY8cUPbjoE2oj4YyYchruVnP7F",
    "client_id": "37055ce5-193c-4af3-952a-1ec35445f6c9",
    "client_secret": "IaS9QoSdZfhqmheWMcTVMRTG6Sndhzupx7Xei46bTs8=",
    "redirect_uri": "http://localhost:8080/oauth/callback"
  }'
