# Vaani

Unofficial prototype: real-time AI voice sales assistant for Surefy (Sankar Group intern assignment). Not an official Surefy product.

Status: in development

## Recording storage

Phase 5 uses a private Supabase Storage bucket named `call-recordings` when
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are configured. The service-role
key is backend-only and must never be added to browser code. For local
development, recordings use the ignored `recordings/` directory when Supabase
is not configured.

Create a private bucket with that name in Supabase Storage, then add these
values to `.env`:

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_BUCKET_NAME=call-recordings
```

The application stores only the object key and content type in PostgreSQL.
Playback uses a five-minute signed URL. Recordings require explicit consent
and are limited by `RECORDING_MAX_BYTES`.
