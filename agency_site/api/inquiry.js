// POST /api/inquiry — receive Studio contact-form submissions, email the owner.
// Env (Vercel project Settings → Environment Variables, then redeploy):
//   AGENCY_SMTP_HOST (default smtp.gmail.com), AGENCY_SMTP_PORT (default 587)
//   AGENCY_SMTP_USER (default storefront.webs@gmail.com)
//   AGENCY_SMTP_PASS (required — Gmail app password)
//   AGENCY_NOTIFY_TO (default storefront.webs@gmail.com)
const nodemailer = require("nodemailer");

// Tiny in-memory rate limit (best-effort on serverless): 5 submits / IP / hour.
const hits = new Map();
function rateLimited(ip) {
  const now = Date.now();
  const arr = (hits.get(ip) || []).filter((t) => now - t < 3600e3);
  arr.push(now);
  hits.set(ip, arr);
  return arr.length > 5;
}

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ ok: false, error: "POST only" });
  }
  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  body = body && typeof body === "object" ? body : {};

  // Honeypot: bots fill it, humans never see it.
  if (body.company_website) {
    return res.status(200).json({ ok: true });
  }

  const name = String(body.name || "").trim().slice(0, 80);
  const business = String(body.business || "").trim().slice(0, 120);
  const email = String(body.email || "").trim().slice(0, 120);
  const phone = String(body.phone || "").trim().slice(0, 40);
  const topic = String(body.topic || "").trim().slice(0, 80);
  const message = String(body.message || "").trim().slice(0, 2000);

  if (!name || !business || !EMAIL_RE.test(email)) {
    return res.status(400).json({ ok: false, error: "Name, business, and a valid email are required." });
  }

  const ip = (req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  if (rateLimited(ip)) {
    return res.status(429).json({ ok: false, error: "Too many requests — please try again later." });
  }

  const pass = process.env.AGENCY_SMTP_PASS;
  if (!pass) {
    console.error("inquiry: AGENCY_SMTP_PASS not configured");
    return res.status(500).json({ ok: false, error: "Form is not configured yet. Please email directly." });
  }
  const host = process.env.AGENCY_SMTP_HOST || "smtp.gmail.com";
  const port = Number(process.env.AGENCY_SMTP_PORT || "587");
  const user = process.env.AGENCY_SMTP_USER || "storefront.webs@gmail.com";
  const to = process.env.AGENCY_NOTIFY_TO || "storefront.webs@gmail.com";

  const text =
    `New Storefront inquiry\n\n` +
    `Name: ${name}\nBusiness: ${business}\nEmail: ${email}\n` +
    `Phone: ${phone || "(not given)"}\nTopic: ${topic || "(not given)"}\n\n` +
    `${message || "(no extra details)"}\n`;

  try {
    const transport = nodemailer.createTransport({
      host, port, secure: port === 465,
      auth: { user, pass },
    });
    await transport.sendMail({
      from: `Storefront inquiries <${user}>`,
      to,
      replyTo: `${name} <${email}>`,
      subject: `New inquiry — ${business} (${name})`,
      text,
    });
    return res.status(200).json({ ok: true });
  } catch (err) {
    console.error("inquiry send failed:", err && err.message);
    return res.status(500).json({ ok: false, error: "Could not send — please email directly." });
  }
};
