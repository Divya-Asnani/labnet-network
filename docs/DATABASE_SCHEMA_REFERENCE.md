# LabNet Supabase Database Schema Reference

This reference documents the live schema of the 7 Supabase PostgreSQL tables in the **LabNet** project, verified via OpenAPI schema inspection and live constraint probing against the database instance (`https://gqvduuonwhbzfismoxbs.supabase.co`).

---

## 1. Summary of Tables & Schema Differences

| Table Name | Live Columns (Verified) | Key Differences vs Expected Mapping |
| :--- | :--- | :--- |
| **`profiles`** | `id`, `display_name`, `role`, `created_at` | `role` restricted by `CHECK (role IN ('admin', 'student'))`. Created via auth trigger. |
| **`devices`** | `id`, `user_id`, `device_name`, `hostname`, `ip_address`, `os`, `status`, `last_seen_at`, `created_at` | Column is `last_seen_at` (not `last_seen`). `status` restricted to `'online'`, `'offline'`. |
| **`chat_messages`** | `id`, `sender_user_id`, `sender_device_id`, `message`, `protocol`, `status`, `sent_at` | Column is `sent_at` (not `created_at`). `protocol` (`'TCP'/'UDP'`) and `status` (`'sent'/'delivered'/'failed'`) are required. |
| **`file_transfers`** | `id`, `sender_user_id`, `sender_device_id`, `receiver_device_id`, `file_name`, `file_size`, `protocol`, `checksum`, `storage_path`, `status`, `started_at`, `completed_at` | Column is `file_name` (not `filename`). Timestamp is `started_at` (not `created_at`). `protocol` must be `'TCP'` or `'UDP'`. |
| **`heartbeat_logs`** | `id`, `device_id`, `received_at`, `latency_ms`, `source_ip` | Matches expected schema. `source_ip` is type `inet`. |
| **`network_sessions`** | `id`, `source_device_id`, `destination_device_id`, `protocol`, `source_port`, `destination_port`, `status`, `started_at`, `ended_at`, `bytes_sent`, `bytes_received` | `status` allows `'active'`, `'completed'`. `protocol` supports `'TCP'`, `'UDP'`, `'ICMP'`, `'HTTP'`, `'DNS'`, `'SMTP'`, `'POP3'`. |
| **`network_events`** | `id`, `device_id`, `event_type`, `protocol`, `source_ip`, `destination_ip`, `details`, `created_at` | `details` is JSONB. `event_type` supports `'heartbeat'`, `'connection'`, `'transfer'`, `'disconnect'`, `'packet_loss'`, `'timeout'`, `'diagnostic'`, `'auth'`. |

---

## 2. Table-by-Table Detailed Specifications

### 2.1 Table: `profiles`
Represents application user profiles linked 1:1 with `auth.users`.
- **`id`** (`uuid`, Primary Key): References `auth.users.id`.
- **`display_name`** (`text`, NOT NULL): Full name or handle of the user.
- **`role`** (`text`, NOT NULL): User role.
  - Constraint: `CHECK (role IN ('admin', 'student'))`
- **`created_at`** (`timestamptz`, DEFAULT `now()`): Creation timestamp.

> **Auth Trigger Behavior:** When a user registers through Supabase Auth (`supabase.auth.sign_up`), the Postgres trigger `on_auth_user_created` automatically inserts a record into `profiles` with `display_name = coalesce(raw_user_meta_data->>'display_name', email)` and `role = 'student'`.

---

### 2.2 Table: `devices`
Tracks network workstations, servers, and nodes active on LabNet.
- **`id`** (`uuid`, Primary Key, DEFAULT `gen_random_uuid()`): Unique device identifier.
- **`user_id`** (`uuid`, NOT NULL): Foreign Key to `profiles.id` (`ON DELETE CASCADE`).
- **`device_name`** (`text`, NOT NULL): Human-readable name (e.g. `'LabNet Server'`, `'Student Workstation'`).
- **`hostname`** (`text`, NULLABLE): Machine hostname (e.g. `'labnet-server'`, `'ubuntu-vm2'`).
- **`ip_address`** (`inet`, NULLABLE): IPv4 address on the subnet (e.g. `'192.168.66.128'`).
- **`os`** (`text`, NULLABLE): Operating system identifier (e.g. `'Ubuntu Linux 22.04'`).
- **`status`** (`text`, NOT NULL): Device reachability status.
  - Constraint: `CHECK (status IN ('online', 'offline'))`
- **`last_seen_at`** (`timestamptz`, NULLABLE): Timestamp of most recent heartbeat or socket interaction.
- **`created_at`** (`timestamptz`, DEFAULT `now()`).

---

### 2.3 Table: `chat_messages`
Persists chat messages exchanged over raw TCP (:5001) or UDP broadcast.
- **`id`** (`uuid`, Primary Key, DEFAULT `gen_random_uuid()`): Unique message identifier.
- **`sender_user_id`** (`uuid`, NOT NULL): Foreign Key to `profiles.id`.
- **`sender_device_id`** (`uuid`, NULLABLE): Foreign Key to `devices.id`.
- **`message`** (`text`, NOT NULL): Message payload text.
- **`protocol`** (`text`, NOT NULL): Transport protocol used.
  - Constraint: `CHECK (protocol IN ('TCP', 'UDP'))`
- **`status`** (`text`, NOT NULL): Delivery state.
  - Constraint: `CHECK (status IN ('sent', 'delivered', 'failed'))`
- **`sent_at`** (`timestamptz`, DEFAULT `now()`): Dispatch timestamp.

> **Schema Limitation & Optional Future Migration (Not Applied Automatically):**
> The current `chat_messages` schema models a shared broadcast channel (matching the TCP broadcast server on port 5001) and does not contain a `recipient_user_id` or `conversation_id` column. LabNet preserves this schema untouched and supports participant-filtered history views alongside the shared General Lab Channel. If strict private 1-to-1 direct messaging is required in a future release, the minimal migration would be:
> ```sql
> ALTER TABLE public.chat_messages
>   ADD COLUMN recipient_user_id uuid REFERENCES public.profiles(id) NULL;
> CREATE INDEX idx_chat_messages_recipient ON public.chat_messages (recipient_user_id, sent_at DESC);
> ```

---

### 2.4 Table: `file_transfers`
Logs all file transfer sessions across the network (TCP port 2121 and UDP port 2122).
- **`id`** (`uuid`, Primary Key, DEFAULT `gen_random_uuid()`): Unique transfer ID.
- **`sender_user_id`** (`uuid`, NOT NULL): Foreign Key to `profiles.id`.
- **`sender_device_id`** (`uuid`, NULLABLE): Foreign Key to `devices.id`.
- **`receiver_device_id`** (`uuid`, NULLABLE): Foreign Key to `devices.id`.
- **`file_name`** (`text`, NOT NULL): Base name of the transferred file.
- **`file_size`** (`int8`, NOT NULL): Total size in bytes.
- **`protocol`** (`text`, NOT NULL):
  - Constraint: `CHECK (protocol IN ('TCP', 'UDP'))`
- **`checksum`** (`text`, NULLABLE): Hexadecimal SHA-256 integrity hash.
- **`storage_path`** (`text`, NULLABLE): Server filesystem path (e.g. `'uploads/test.pdf'`).
- **`status`** (`text`, NOT NULL):
  - Constraint: `CHECK (status IN ('pending', 'in_progress', 'completed', 'failed'))`
- **`started_at`** (`timestamptz`, DEFAULT `now()`): Transfer initiation time.
- **`completed_at`** (`timestamptz`, NULLABLE): Transfer completion/verification time.

---

### 2.5 Table: `heartbeat_logs`
Records historical heartbeat datagrams received by `server/udp_server.py` on port 6000.
- **`id`** (`uuid`, Primary Key, DEFAULT `gen_random_uuid()`).
- **`device_id`** (`uuid`, NOT NULL): Foreign Key to `devices.id`.
- **`received_at`** (`timestamptz`, DEFAULT `now()`): Timestamp when the UDP datagram was processed.
- **`latency_ms`** (`numeric`, NULLABLE): Measured network round-trip or transit latency in milliseconds.
- **`source_ip`** (`inet`, NULLABLE): Source IPv4 address of the datagram.

---

### 2.6 Table: `network_sessions`
Tracks active and completed network connections across all transport protocols.
- **`id`** (`uuid`, Primary Key, DEFAULT `gen_random_uuid()`).
- **`source_device_id`** (`uuid`, NULLABLE): Foreign Key to `devices.id`.
- **`destination_device_id`** (`uuid`, NULLABLE): Foreign Key to `devices.id`.
- **`protocol`** (`text`, NOT NULL):
  - Constraint: `CHECK (protocol IN ('TCP', 'UDP', 'ICMP', 'HTTP', 'DNS', 'SMTP', 'POP3'))`
- **`source_port`** (`int4`, NULLABLE): Ephemeral source port.
- **`destination_port`** (`int4`, NULLABLE): Destination service port (e.g. 5001, 2121, 2122, 6000, 5050).
- **`status`** (`text`, NOT NULL):
  - Constraint: `CHECK (status IN ('active', 'completed'))`
- **`started_at`** (`timestamptz`, DEFAULT `now()`).
- **`ended_at`** (`timestamptz`, NULLABLE).
- **`bytes_sent`** (`int8`, DEFAULT 0).
- **`bytes_received`** (`int8`, DEFAULT 0).

---

### 2.7 Table: `network_events`
Audit log of network events, status transitions, diagnostic runs, and protocol errors.
- **`id`** (`uuid`, Primary Key, DEFAULT `gen_random_uuid()`).
- **`device_id`** (`uuid`, NULLABLE): Foreign Key to `devices.id`.
- **`event_type`** (`text`, NOT NULL):
  - Constraint: `CHECK (event_type IN ('heartbeat', 'connection', 'transfer', 'disconnect', 'packet_loss', 'timeout', 'diagnostic', 'auth'))`
- **`protocol`** (`text`, NULLABLE): Associated protocol string.
- **`source_ip`** (`inet`, NULLABLE): Origin IP address.
- **`destination_ip`** (`inet`, NULLABLE): Destination IP address.
- **`details`** (`jsonb`, NULLABLE): Structured JSON metadata (e.g. checksums, retry counts, latency).
- **`created_at`** (`timestamptz`, DEFAULT `now()`).
