import json
import email_generator as eg
import current_lead_generator as clg

leads = json.load(open("target_leads.json", encoding="utf-8"))
lead = next(l for l in leads if l["lead_id"] == "lead_00001")
biz = clg.build_current_lead(
    lead, {"preview_url": "https://la-gloria-modern-mexican-bar.vercel.app"}, None)
print("biz keys:", sorted(biz.keys()))
prompt = eg.build_prompt(biz, [], "initial_outreach")
print("prompt has rating:", '"rating": 4.7' in prompt, "| site_note:", "site_note" in prompt)
res = eg.main(lead="lead_00001", current="current_leads.json",
              send=False, history="email_history.json")
print("=" * 70)
print("SUBJECT:", res["subject"])
print(res["body"])
print("WORDS:", len(res["body"].split()))
