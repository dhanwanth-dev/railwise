# Submission checklist — Razorpay AI Buildathon 2026

**Track:** 03 — AI Revenue Recovery  
**Deadline:** 5 September 2026  
**Product:** Railwise

## Artifacts required

1. **Public GitHub repo** — push `railwise/` (this folder)  
2. **Architecture** — already in `docs/ARCHITECTURE.md` (link it in the form)  
3. **5-minute pitch video** — follow `docs/DEMO_SCRIPT.md`  
4. **Application form** — https://razorpay.com/buildathon/

## Before you record

```bash
cd backend && source .venv/bin/activate
pytest tests/edge_cases -q          # expect 15 passed
uvicorn app.main:app --port 8000

# other terminal
cd frontend && npm run dev
```

In the cockpit: run the 500-failure batch, open the featured edge case (`UPI budget exhausted → rail_switch`), show 0 hard waste / 0 UPI violations.

## Pitch one-liners

- Deepens Intelligent Revenue-Protect’s decision lattice — does not claim Razorpay “missed” retries.  
- Compliance ceilings beat recoverability scores.  
- AI only for ambiguous codes + legal timing slots.  
- USP = edge-case pytest suite, not a chat agent.

## Do not include

- Lorryman or any unrelated Play Store app  
- Claims of production-scale validation  
- Live money / real customer data  

## After panel invite

Be ready to walk any fixture in `docs/EDGE_CASES.md` and explain `docs/WHY.md` file-by-file.
