# Documentation assets

Reserved for documentation images and other static assets. Do not place private
or identifying user data here.

## Day 12 chat screenshots — expected, not yet generated

The Day 12 Playwright suite writes these four files. **They are not in the
repository yet**: the sandbox that built Day 12 has no browser binary
(`npx playwright install chromium` cannot reach `cdn.playwright.dev`) and no apt
access, so `e2e/chat.spec.ts` was written and listed but never run. Generate them
on a machine with browsers:

```bash
# 1. the API, with the offline Fake model
cd backend
LLM_PROVIDER=fake SAFETY_ML_ENABLED=false APP_ENV=development \
SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=$(python -c \
  'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())') \
  make dev          # after `alembic upgrade head`

# 2. the browser tests, which write the screenshots below
cd frontend && npm run test:e2e
```

| file | what it shows |
| --- | --- |
| `day12-chat-mobile-360x640.png` | an ordinary exchange at 360x640, light palette |
| `day12-chat-dark-mobile-360x640.png` | the same conversation in dark mode |
| `day12-chat-crisis-mobile-360x640.png` | a HIGH-risk reply: the CrisisCard under the message, page softened |
| `day12-chat-desktop-1280x800.png` | the desktop layout (side rail), light palette |
| `day12-chat-dark-desktop-1280x800.png` | the same, dark palette |
| `day12-chat-crisis-desktop-1280x800.png` | the crisis card at desktop width |

The screenshots contain only synthetic conversation text (the three starter
sentences and `I want to kill myself`, used as the safety test phrase) and only
the synthetic `555…` / international helpline entries the shipped content file
serves for the `DEFAULT` region. No real person's words, ever.
