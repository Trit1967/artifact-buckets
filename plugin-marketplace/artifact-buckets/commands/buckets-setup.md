---
description: Connect Claude Code to Artifact Buckets (signs in through your browser) and check it works
---

Connect this Claude Code to Artifact Buckets and confirm it works. Follow these steps in order and keep the person informed in plain words.

1. Call the `list_writable_folders` tool from the `artifact-buckets` server.
2. If the tool is not available, or the call fails because sign-in is needed, tell the person:
   "Artifact Buckets needs you to sign in once. Run `/mcp`, choose **artifact-buckets**, then **Authenticate**. Your browser opens the Artifact Buckets sign-in; sign in with your work account and press **Connect**. Then run `/buckets-setup` again."
   Stop here.
3. If the call works, tell the person they're connected, and show:
   - their home folder (the first folder in the result, `/users/<name>`),
   - the team folders they can save to (the other `/shared/...` entries),
   - that files from a repository go to `/users/<name>/projects/<repo>` unless their team has a routing rule, and loose files go to their inbox.
4. Offer one example they can try, such as "save this file to my buckets".

If the server address is wrong for this lab, the person can set the environment variable `ARTIFACT_BUCKETS_URL` (for example `https://buckets.lab.local`) before starting Claude Code.
