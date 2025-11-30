## Vulnerability Information
I have identified a widespread concurrency flaw in the handling of OAuth2 token exchanges. Specifically, the backend logic suffers from a Time-of-Check to Time-of-Use (TOCTOU) discrepancy when validating grants.

When the server receives simultaneous calls to exchange credentials, it fails to lock the database state quickly enough. Consequently, it is possible to "mint" multiple valid sessions from a single authorization grant before the server invalidates it.

I have verified this behavior across multiple endpoints. The core issue is that the atomicity of the transaction is not preserved, allowing an attacker to bypass the intended one-time-use constraints of the protocol. This results in a persistent access scenario that survives standard revocation attempts because the system believes only one token exists, when in reality, a pool of valid tokens has been created.

## Race Condition for Access Token
The OAuth2 specification strictly mandates that an authorization code must be consumed exactly once. However, due to the lack of proper transactional isolation, this limit can be circumvented.

By flooding the token endpoint with parallel requests carrying the same auth code, the server processes several of them as valid before the first transaction commits the "code used" state to the database. This yields multiple distinct, valid access tokens.

## Proof-Of-Concept
*Step-by-step reproduction guide:*

1.  Initiate the standard OAuth flow and pause the interception proxy right after authentication to grab the `?code=XYZ` parameter.
2.  Do not forward the request yet. Instead, prepare a script to perform a request smuggling or parallel burst attack.
3.  Execute a burst of 30 HTTP POST requests simultaneously targeting the `/oauth/token` endpoint.

**Bash script example:**
```bash
for i in {1..30}; do
  curl -X POST [https://api.target.com/oauth/token](https://api.target.com/oauth/token) \\
  -d "client_id=MY_ID&client_secret=MY_SECRET&grant_type=authorization_code&code=THE_STOLEN_CODE" &
done
wait
```
Observation: You will notice that the server responds with 200 OK for multiple requests (e.g., 5 out of 30).
Verification: Each response contains a unique access_token. Verify that all of them can query the user profile endpoint (/api/v1/user). They will all return data successfully.

## Race Condition for Refresh Token

The same concurrency gap applies to the token renewal process. A refresh token is typically meant to be rotated or invalidated upon use. However, the system allows the same refresh token to be reused if the requests arrive within a specific millisecond window.

**Attack Flow:**
1.  Take a valid `refresh_token` obtained from a legitimate login.
2.  Use a multi-threaded tool (like Turbo Intruder in Burp Suite) to send 50 concurrent renewal requests using that single refresh token.
3.  The server will issue multiple new `access_token` / `refresh_token` sets.
4.  Crucially, these "ghost" tokens are often not tracked correctly in the session manager.

**Revocation Test:**
If the user goes to their dashboard and clicks "Disconnect App", the system typically only invalidates the last issued token or the original one. The other N-1 tokens generated via the race condition remain active, allowing the attacker to maintain persistence indefinitely.

## Impact

This vulnerability completely neutralizes the security controls of the OAuth implementation. By exploiting this synchronization error, an attacker can:
*   **Bypass Revocation:** A user believes they have secured their account by revoking an app, but the attacker retains access.
*   **Violate RFC Compliance:** The service fails to adhere to the strict one-time-use policy for codes and tokens.
*   **Infinite Persistence:** By continuously racing the refresh token endpoint, an attacker can maintain a "Hydra-like" session where cutting off one head (token) does not kill the session.

## Put it simply

Imagine you give a house key to a guest. When they leave, you ask for the key back (revocation). They hand you a key, but because the locksmith (the server) allowed them to make 10 copies instantly while you weren't looking, they can still enter your house whenever they want. The system fails to realize that multiple keys were created from the single authorization permission.

## Attachments

No attachments
