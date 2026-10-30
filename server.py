#!/usr/bin/env python3
"""OuttaPocketTV Skit Studio: stateless web service; Stripe is order/customer ledger.

No local customer database or persistent disk. All payment status is retrieved from
Stripe; each free image is sent directly to the browser. Secrets are server-side.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import io
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
API_BASE = 'https://api.stripe.com/v1'
PRICE_CENTS = 2999  # Legacy default
TIERS = {
    15: {'price_cents': 2999, 'label': 'Up to 15 seconds'},
    30: {'price_cents': 4999, 'label': 'Up to 30 seconds'},
    60: {'price_cents': 8999, 'label': 'Up to 1 minute'},
}
PRODUCT_KEY = 'optv_custom_skit_v1'
STYLES = {'Cinematic comedy', 'Photoreal absurdity', 'Animated chaos', '90s sitcom', 'Mockumentary'}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=STATIC), name='static')
_guard = threading.RLock()
_attempts: dict[str, deque[float]] = defaultdict(deque)


def env(name, default=''):
    return os.environ.get(name, default).strip()


def base_url():
    host = env('RENDER_EXTERNAL_HOSTNAME')
    url = env('PUBLIC_BASE_URL') or ('https://' + host if host else 'http://localhost:8080')
    return url.rstrip('/')


def configured():
    return bool(env('OPENAI_API_KEY') and env('STRIPE_SECRET_KEY') and env('APP_SIGNING_SECRET'))


def payment_ready():
    return bool(env('STRIPE_SECRET_KEY') and env('APP_SIGNING_SECRET')
                and env('STRIPE_WEBHOOK_SECRET'))


class PreviewInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    idea: str = Field(min_length=18, max_length=450)
    style: str = Field(default='Cinematic comedy', max_length=30)


class CheckoutInput(BaseModel):
    preview_token: str = Field(min_length=20, max_length=2400)
    instructions: str = Field(default='', max_length=450)
    duration_seconds: int = Field(default=15)


class DirectCheckoutInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    idea: str = Field(min_length=18, max_length=450)
    style: str = Field(default='Cinematic comedy', max_length=30)
    instructions: str = Field(default='', max_length=450)
    duration_seconds: int = Field(default=15)


class CustomRequestInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    idea: str = Field(min_length=18, max_length=450)
    style: str = Field(default='Cinematic comedy', max_length=30)
    duration_seconds: int = Field(gt=60, le=21600)
    instructions: str = Field(default='', max_length=450)


class QuoteHandledInput(BaseModel):
    customer_id: str = Field(pattern=r'^cus_[A-Za-z0-9]{8,100}$')


class DeliveredInput(BaseModel):
    session_id: str = Field(pattern=r'^cs_(test_)?[A-Za-z0-9_]{12,160}$')


class ContactInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    message: str = Field(min_length=8, max_length=1200)


def valid_email(raw):
    value = raw.strip().lower()
    if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]{2,}', value) or
            '\r' in value or '\n' in value):
        raise HTTPException(400, 'Enter a valid email address.')
    return value


def stripe(method, path, fields=None, *, idempotency=None):
    key = env('STRIPE_SECRET_KEY')
    if not key:
        raise HTTPException(503, 'Secure checkout is not available yet.')
    headers = {'Authorization': 'Bearer ' + key}
    if idempotency:
        headers['Idempotency-Key'] = idempotency
    try:
        response = requests.request(method, API_BASE + path,
                                    params=fields if method == 'GET' else None,
                                    data=fields if method != 'GET' else None,
                                    headers=headers, timeout=20)
    except requests.RequestException:
        raise HTTPException(503, 'Payment service unavailable. Please try later.')
    if response.status_code >= 400:
        print('Stripe API error', response.status_code, path.split('?')[0], flush=True)
        raise HTTPException(503, 'Payment service unavailable. Please try later.')
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, 'Payment service returned an invalid response.')


def client_ip(request: Request) -> str:
    """Render's edge supplies the visitor address in X-Forwarded-For."""
    if env('RENDER_EXTERNAL_HOSTNAME'):
        forwarded = request.headers.get('x-forwarded-for', '')
        if forwarded:
            candidate = forwarded.split(',')[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return request.client.host if request.client else 'unknown'


def rate_limit(ip: str, *, scope='preview', limit=3):
    # Per-IP burst protection; one free sample per email is persisted in Stripe.
    # The in-memory IP buckets reset when Render restarts; no global daily cap.
    hashed = hashlib.sha256((env('APP_SIGNING_SECRET') + scope + ip).encode()).hexdigest()
    ts = time.time()
    with _guard:
        dq = _attempts[hashed]
        while dq and dq[0] < ts - 3600:
            dq.popleft()
        if len(dq) >= limit:
            raise HTTPException(429, 'Too many requests from this connection. Try later.')
        dq.append(ts)


def customer_for_email(email):
    # Stripe's exact-match customer listing (not eventually consistent search).
    rows = stripe('GET', '/customers', {'email': email, 'limit': 100}).get('data', [])
    our = [r for r in rows if (r.get('metadata') or {}).get('optv_sample_status')]
    if our:
        return our[0]
    # Creating a dedicated preview contact means we never modify customers
    # created by unrelated businesses on the same Stripe account.
    idem = 'optv-sample-customer-' + hashlib.sha256(email.encode()).hexdigest()
    return stripe('POST', '/customers', {'email': email,
                 'metadata[optv_sample_status]': 'new',
                 'description': 'OuttaPocketTV free sample request'}, idempotency=idem)



def customer_state(customer_id, status):
    return stripe('POST', '/customers/' + customer_id,
                  {'metadata[optv_sample_status]': status,
                   'metadata[optv_sample_updated]': str(int(time.time()))})


def font(size, bold=False):
    paths = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else
             '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def brand_image(source: bytes, idea: str, style: str) -> bytes:
    with Image.open(io.BytesIO(source)) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError('Unexpected oversized image')
        canvas = ImageOps.fit(image.convert('RGB'), (768, 1024), method=Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas, 'RGBA')
    draw.rectangle((0, 0, 768, 144), fill=(8, 8, 8, 220))
    draw.rectangle((0, 848, 768, 1024), fill=(8, 8, 8, 222))
    draw.text((36, 874), 'FREE SAMPLE • PREVIEW ONLY', font=font(26, True), fill=(255, 208, 44, 255))
    draw.text((36, 927), style.upper(), font=font(18, True), fill=(255, 255, 255, 255))
    with Image.open(STATIC / 'optv-logo.webp') as mark:
        logo = ImageOps.fit(mark.convert('RGBA'), (117, 117))
        canvas.paste(logo, (24, 14), logo)
    draw = ImageDraw.Draw(canvas, 'RGBA')
    draw.text((158, 38), 'OUTTA POCKET TV', font=font(31, True), fill=(255, 211, 32, 255))
    draw.text((160, 88), 'CERTIFIED NONSENSE', font=font(20, True), fill=(255, 255, 255, 255))
    buffer = io.BytesIO()
    canvas.save(buffer, format='JPEG', quality=83, optimize=True)
    return buffer.getvalue()


def generate_scene(idea, style, email):
    key = env('OPENAI_API_KEY')
    if not key:
        raise HTTPException(503, 'AI previews are not available yet.')
    prompt = ('A single visually funny, cinematic vertical still for an original absurd comedy skit. '
              'Clear humorous action, distinctive characters, strong framing, expressive faces, '
              'premium production quality. Do not render text, logos or watermarks. '
              'No imitation of identifiable living people. Visual style: ' + style + '. Scene: ' + idea)
    identity = hmac.new(env('APP_SIGNING_SECRET').encode(), email.encode(), hashlib.sha256).hexdigest()
    try:
        response = requests.post('https://api.openai.com/v1/images/generations',
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                                 json={'model': env('OPENAI_IMAGE_MODEL', 'gpt-image-2.5-flare'),
                                       'prompt': prompt, 'size': '1024x1536', 'quality': 'low',
                                       'n': 1, 'safety_identifier': identity}, timeout=90)
        if response.status_code != 200:
            print('Image provider status', response.status_code, flush=True)
            raise HTTPException(503, 'Image generation is temporarily unavailable.')
        raw = base64.b64decode(response.json()['data'][0]['b64_json'], validate=True)
        if len(raw) > 15_000_000:
            raise ValueError('Image too large')
        return brand_image(raw, idea, style)
    except HTTPException:
        raise
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(503, 'Image generation is temporarily unavailable.')


def make_token(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()).rstrip(b'=')
    sig = hmac.new(env('APP_SIGNING_SECRET').encode(), encoded, hashlib.sha256).digest()
    return encoded.decode() + '.' + base64.urlsafe_b64encode(sig).rstrip(b'=').decode()


def read_token(token):
    if not env('APP_SIGNING_SECRET') or len(token) > 2400:
        raise HTTPException(400, 'Preview is expired or invalid.')
    try:
        body, signed = token.split('.')
        sig = base64.urlsafe_b64decode(signed + '=' * (-len(signed) % 4))
        good = hmac.new(env('APP_SIGNING_SECRET').encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, good):
            raise ValueError('Bad signature')
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if data.get('v') != 1 or abs(time.time() - int(data['t'])) > 3 * 86400:
            raise ValueError('Expired token')
        if data.get('s') not in STYLES or len(data.get('i', '')) > 450:
            raise ValueError('Bad payload')
        valid_email(data['e'])
        return data
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, 'Preview is expired or invalid. Make a new request if needed.')


def authorized(request):
    expected = env('ADMIN_TOKEN')
    bearer = request.headers.get('Authorization', '')
    if not expected or not secrets.compare_digest(bearer, 'Bearer ' + expected):
        raise HTTPException(401, 'Owner authorization required.')


@app.middleware('http')
async def headers(request, call_next):
    if request.method in {'POST', 'PUT', 'PATCH'}:
        length = request.headers.get('content-length')
        if length is None or not length.isdigit() or int(length) > 8192:
            return JSONResponse({'detail': 'Request size exceeds limit.'}, status_code=413)
    response = await call_next(request)
    response.headers.update({
        'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        'Content-Security-Policy': (
            # Google's AdSense verification loader is needed on the public homepage.
            # Retain the existing restrictive policy on checkout, admin and API endpoints.
            ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
             "style-src 'self' 'unsafe-inline' https:; "
             "img-src 'self' data: blob: https:; "
             "frame-src https:; connect-src 'self' https:; "
             "form-action 'self' https://checkout.stripe.com; base-uri 'self'; object-src 'none'")
            if request.url.path == '/' else
            ("default-src 'self'; script-src 'self'; "
             "style-src 'self'; img-src 'self' data: blob:; "
             "frame-src 'none'; connect-src 'self'; "
             "form-action 'self' https://checkout.stripe.com; base-uri 'self'; object-src 'none'")
        ),
        'Cache-Control': 'no-store' if request.url.path.startswith('/api/') else 'public, max-age=180',
    })
    if (request.url.path in {'/order', '/admin'} or
            request.url.path.startswith('/api/') or
            request.url.path.startswith('/static/admin')):
        # Payment confirmations, owner tools and API responses are not
        # publisher articles and should not appear in search results.
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    if request.headers.get('x-forwarded-proto') == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html', media_type='text/html')


# Publisher information pages intentionally contain no AdSense ad tags.
# The homepage remains the only page loading the AdSense verification script.
@app.get('/about')
@app.get('/about/')
def about_page():
    return FileResponse(STATIC / 'about.html', media_type='text/html')


@app.get('/privacy')
@app.get('/privacy/')
def privacy_page():
    return FileResponse(STATIC / 'privacy.html', media_type='text/html')


@app.get('/terms')
@app.get('/terms/')
def terms_page():
    return FileResponse(STATIC / 'terms.html', media_type='text/html')


@app.get('/contact')
@app.get('/contact/')
def contact_page():
    return FileResponse(STATIC / 'contact.html', media_type='text/html')


@app.get('/robots.txt')
def robots_txt():
    return FileResponse(STATIC / 'robots.txt', media_type='text/plain')


@app.get('/sitemap.xml')
def sitemap_xml():
    return FileResponse(STATIC / 'sitemap.xml', media_type='application/xml')


@app.get('/ads.txt')
def ads_txt():
    return FileResponse(STATIC / 'ads.txt', media_type='text/plain')


@app.get('/order')
def order_page():
    return FileResponse(STATIC / 'order.html', media_type='text/html')


@app.get('/admin')
def admin_page():
    return FileResponse(STATIC / 'admin.html', media_type='text/html')


@app.get('/healthz')
def health():
    return {'ok': True}


@app.get('/api/config')
def config():
    return {'preview_enabled': configured(), 'checkout_enabled': payment_ready(),
            'price_cents': PRICE_CENTS, 'tiers': [
                {'duration_seconds': seconds, **tier} for seconds, tier in TIERS.items()],
            'fulfillment': 'manual_email', 'version': '1.2'}


@app.post('/api/preview')
def preview(item: PreviewInput, request: Request):
    if not configured():
        raise HTTPException(503, 'Free preview generation is not enabled yet.')
    email, idea, style = valid_email(item.email), item.idea.strip(), item.style.strip()
    if len(idea) < 18 or style not in STYLES:
        raise HTTPException(400, 'Enter an original skit idea and choose a valid style.')
    rate_limit(client_ip(request))
    with _guard:
        customer = customer_for_email(email)
        meta = customer.get('metadata') or {}
        state = meta.get('optv_sample_status')
        last = int(meta.get('optv_sample_updated') or '0')
        if state == 'used' or (state == 'reserved' and time.time() - last < 1200):
            raise HTTPException(409, 'A free sample has already been requested for this email.')
        customer_state(customer['id'], 'reserved')
    try:
        picture = generate_scene(idea, style, email)
    except Exception:
        try:
            customer_state(customer['id'], 'failed')
        except Exception:
            pass
        raise
    # If recording delivery fails, keep 'reserved' rather than opening a free retry.
    customer_state(customer['id'], 'used')
    token = make_token({'v': 1, 'e': email, 'i': idea, 's': style, 't': int(time.time())})
    return {'preview_token': token, 'preview_image': 'data:image/jpeg;base64,' + base64.b64encode(picture).decode(),
            'mode': 'ai', 'price_cents': PRICE_CENTS}


def selected_tier(duration_seconds):
    tier = TIERS.get(duration_seconds)
    if not tier:
        raise HTTPException(400, 'Choose one of the available video lengths.')
    return tier


def create_checkout_session(detail, notes, fingerprint, duration_seconds=15):
    tier = selected_tier(duration_seconds)
    # Idempotency within 20-minute windows avoids accidental duplicate sessions
    # but lets previous customers order another skit in future.
    fingerprint = hashlib.sha256((fingerprint + str(duration_seconds) + str(int(time.time() // 1200))).encode()).hexdigest()
    fields = {
        'mode': 'payment', 'customer_email': detail['e'],
        'branding_settings[display_name]': 'OuttaPocketTV',
        'client_reference_id': 'optv-' + fingerprint[:28],
        'success_url': base_url() + '/order?session_id={CHECKOUT_SESSION_ID}',
        'cancel_url': base_url() + '/?checkout=cancelled',
        'line_items[0][quantity]': '1',
        'line_items[0][price_data][currency]': 'usd',
        'line_items[0][price_data][unit_amount]': str(tier['price_cents']),
        'line_items[0][price_data][product_data][name]':
            'OuttaPocketTV custom comedy skit (' + tier['label'] + ')',
        'metadata[optv_product]': PRODUCT_KEY, 'metadata[idea]': detail['i'],
        'metadata[style]': detail['s'], 'metadata[instructions]': notes,
        'metadata[duration_seconds]': str(duration_seconds),
        'metadata[fulfillment]': 'awaiting_manual_delivery',
    }
    session = stripe('POST', '/checkout/sessions', fields, idempotency='optv-checkout-' + fingerprint)
    url = session.get('url', '')
    if not url.startswith('https://checkout.stripe.com/'):
        raise HTTPException(502, 'Secure checkout could not be started.')
    return {'checkout_url': url}


@app.post('/api/checkout')
def checkout(item: CheckoutInput):
    if not payment_ready():
        raise HTTPException(503, 'Secure checkout is not enabled yet.')
    detail = read_token(item.preview_token)
    notes = item.instructions.strip()
    return create_checkout_session(detail, notes, item.preview_token + notes, item.duration_seconds)


@app.post('/api/checkout-direct')
def checkout_direct(item: DirectCheckoutInput, request: Request):
    # Allow purchases after a browser tab is closed or a free preview was used.
    if not payment_ready():
        raise HTTPException(503, 'Secure checkout is not available yet.')
    email = valid_email(item.email)
    idea, style = item.idea.strip(), item.style.strip()
    if len(idea) < 18 or style not in STYLES:
        raise HTTPException(400, 'Enter an original skit idea and choose a valid style.')
    rate_limit(client_ip(request), scope='checkout', limit=15)
    notes = item.instructions.strip()
    return create_checkout_session({'e': email, 'i': idea, 's': style}, notes, email + idea + style + notes, item.duration_seconds)


@app.post('/api/custom-request')
def create_custom_request(item: CustomRequestInput, request: Request):
    # Custom-length quote requests persist in the same Stripe account as orders,
    # but are separate from paid orders and never initiate a charge.
    if not env('STRIPE_SECRET_KEY'):
        raise HTTPException(503, 'Custom requests are temporarily unavailable.')
    email = valid_email(item.email)
    idea, style, notes = item.idea.strip(), item.style.strip(), item.instructions.strip()
    if len(idea) < 18 or style not in STYLES:
        raise HTTPException(400, 'Enter a skit idea and choose a valid style.')
    rate_limit(client_ip(request), scope='custom_quote', limit=5)
    signature = email + idea + style + str(item.duration_seconds) + notes
    digest = hashlib.sha256((signature + str(int(time.time() // 1200))).encode()).hexdigest()
    customer = stripe('POST', '/customers', {
        'email': email,
        'description': 'OuttaPocketTV custom-length video quote request',
        'metadata[optv_custom_request]': 'pending',
        'metadata[requested_duration_seconds]': str(item.duration_seconds),
        'metadata[idea]': idea,
        'metadata[style]': style,
        'metadata[instructions]': notes,
    }, idempotency='optv-custom-' + digest)
    return {'received': True, 'request_id': customer['id'],
            'message': 'Request received. We will review the details and contact you by email about a custom quote.'}


def get_order(session_id):
    if not re.fullmatch(r'cs_(?:test_)?[A-Za-z0-9_]{12,160}', session_id):
        raise HTTPException(404, 'Order not found.')
    obj = stripe('GET', '/checkout/sessions/' + session_id)
    if (obj.get('metadata') or {}).get('optv_product') != PRODUCT_KEY:
        raise HTTPException(404, 'Order not found.')
    return obj


@app.get('/api/order/{session_id}')
def order_status(session_id: str):
    obj = get_order(session_id)
    paid = obj.get('payment_status') == 'paid'
    return {'status': ('delivered' if obj.get('metadata', {}).get('delivered_at') else 'paid') if paid else 'unpaid',
            'paid': paid, 'amount_cents': obj.get('amount_total')}


def verified_stripe_event(payload: bytes, signature: str):
    """Verify Stripe's timestamped signature before trusting a payment event."""
    secret = env('STRIPE_WEBHOOK_SECRET')
    if not secret:
        raise HTTPException(503, 'Payment notifications are not configured.')
    fields = {}
    for segment in signature.split(','):
        key, separator, value = segment.strip().partition('=')
        if separator:
            fields.setdefault(key, []).append(value)
    try:
        timestamp = int(fields.get('t', [''])[0])
        if abs(time.time() - timestamp) > 300:
            raise ValueError('Webhook timestamp outside tolerance')
        expected = hmac.new(secret.encode(), str(timestamp).encode() + b'.' + payload,
                            hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in fields.get('v1', [])):
            raise ValueError('Webhook signature mismatch')
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise ValueError('Webhook is not a JSON object')
        return event
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, 'Invalid payment event signature.')


@app.post('/api/stripe/webhook')
async def stripe_webhook(request: Request):
    event = verified_stripe_event(await request.body(), request.headers.get('Stripe-Signature', ''))
    if event.get('type') not in {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}:
        return {'received': True}
    session = (event.get('data') or {}).get('object') or {}
    session_id = session.get('id', '')
    if not isinstance(session_id, str) or not re.fullmatch(r'cs_(?:test_)?[A-Za-z0-9_]{12,160}', session_id):
        return {'received': True}
    if (session.get('metadata') or {}).get('optv_product') != PRODUCT_KEY or session.get('payment_status') != 'paid':
        return {'received': True}
    # Stripe remains the durable order ledger. A repeated webhook writes no new fulfillment action.
    actual = get_order(session_id)
    if actual.get('payment_status') != 'paid':
        raise HTTPException(409, 'Payment has not settled yet.')
    if (actual.get('metadata') or {}).get('optv_payment_event_id') != event.get('id'):
        stripe('POST', '/checkout/sessions/' + session_id, {
            'metadata[optv_payment_event_id]': str(event.get('id', ''))[:160],
            'metadata[optv_payment_verified_at]': datetime.now(timezone.utc).isoformat(),
        }, idempotency='optv-confirm-' + hashlib.sha256(str(event.get('id', '')).encode()).hexdigest())
    return {'received': True}


@app.get('/api/admin/orders')
def admin_orders(request: Request):
    authorized(request)
    found = []
    cursor = None
    for _ in range(4):
        params = {'limit': 100}
        if cursor:
            params['starting_after'] = cursor
        batch = stripe('GET', '/checkout/sessions', params)
        for session in batch.get('data', []):
            meta = session.get('metadata') or {}
            if meta.get('optv_product') != PRODUCT_KEY or session.get('payment_status') != 'paid':
                continue
            found.append({
                'id': session['id'], 'email': (session.get('customer_details') or {}).get('email') or session.get('customer_email') or '',
                'amount_cents': session.get('amount_total'), 'idea': meta.get('idea', ''),
                'style': meta.get('style', ''), 'instructions': meta.get('instructions', ''),
                'duration_seconds': int(meta.get('duration_seconds') or 15),
                'delivered_at': meta.get('delivered_at'), 'created_at': session.get('created'),
            })
        if not batch.get('has_more') or not batch.get('data'):
            break
        cursor = batch['data'][-1]['id']
    return {'orders': found, 'note': 'Orders and fulfillment flags are stored in Stripe, not on Render.'}


@app.get('/api/admin/custom-requests')
def admin_custom_requests(request: Request):
    authorized(request)
    found = []
    page = None
    # Search only OuttaPocketTV requests, not other businesses' customers.
    for _ in range(5):
        params = {'query': "metadata['optv_custom_request']:'pending' OR "
                           "metadata['optv_custom_request']:'contacted'",
                  'limit': 100}
        if page:
            params['page'] = page
        batch = stripe('GET', '/customers/search', params)
        for customer in batch.get('data', []):
            meta = customer.get('metadata') or {}
            found.append({
                'id': customer['id'], 'email': customer.get('email') or '',
                'duration_seconds': int(meta.get('requested_duration_seconds') or 0),
                'idea': meta.get('idea', ''), 'style': meta.get('style', ''),
                'instructions': meta.get('instructions', ''),
                'status': meta.get('optv_custom_request'),
                'created_at': customer.get('created'),
            })
        if not batch.get('has_more') or not batch.get('next_page'):
            break
        page = batch['next_page']
    return {'requests': sorted(found, key=lambda item: item['created_at'] or 0, reverse=True)}


@app.post('/api/admin/custom-request-contacted')
def mark_quote_contacted(item: QuoteHandledInput, request: Request):
    authorized(request)
    customer = stripe('GET', '/customers/' + item.customer_id)
    if (customer.get('metadata') or {}).get('optv_custom_request') not in {'pending', 'contacted'}:
        raise HTTPException(404, 'Custom request not found.')
    stripe('POST', '/customers/' + item.customer_id,
           {'metadata[optv_custom_request]': 'contacted'})
    return {'contacted': True}


@app.post('/api/admin/delivered')
def mark_delivered(item: DeliveredInput, request: Request):
    authorized(request)
    obj = get_order(item.session_id)
    if obj.get('payment_status') != 'paid':
        raise HTTPException(409, 'Cannot mark unpaid order delivered.')
    stamp = (obj.get('metadata') or {}).get('delivered_at') or datetime.now(timezone.utc).isoformat()
    stripe('POST', '/checkout/sessions/' + item.session_id,
           {'metadata[delivered_at]': stamp, 'metadata[fulfillment]': 'delivered_by_email'})
    return {'delivered': True, 'sent_by_site': False, 'note': 'This records delivery only. You must email the MP4 to the customer separately.'}


@app.post('/api/contact')
def contact(item: ContactInput, request: Request):
    # No hidden contact database. Uses optional transactional-mail API.
    to = env('SUPPORT_EMAIL')
    key = env('RESEND_API_KEY')
    sender = env('RESEND_FROM_EMAIL')
    if not (to and key and sender):
        raise HTTPException(503, 'Contact email is not enabled yet.')
    rate_limit(client_ip(request), scope='contact')
    sender_email = valid_email(item.email)
    body = {'from': sender, 'to': [to], 'subject': 'OuttaPocketTV website inquiry',
            'text': 'Reply to: ' + sender_email + '\n\n' + item.message.strip(),
            'reply_to': sender_email}
    try:
        res = requests.post('https://api.resend.com/emails', headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, json=body, timeout=15)
        if res.status_code not in (200, 201):
            raise ValueError('Mail rejected')
    except (requests.RequestException, ValueError):
        raise HTTPException(503, 'Contact service is unavailable. Please retry later.')
    return {'received': True}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(env('PORT', '8080')))
