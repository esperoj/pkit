type save_options = {
  capture_all: bool; [@default false]
  capture_outlinks: bool; [@default false]
  email_result: bool; [@default true]
  force_get: bool; [@default true]
  skip_first_archive: bool; [@default true]
} [@@deriving yojson { strict = false }]

type save_result = {
  url: string;
  archive_url: string;
  job_id: string;
} [@@deriving yojson]

type job_status_response = {
  status: string;
  timestamp: string option; [@default None]
  original_url: string option; [@default None]
  error: string option; [@default None]
  message: string option; [@default None]
} [@@deriving yojson { strict = false }]
