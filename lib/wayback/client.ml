module Types = Types

(* Polymorphic variants for explicit, type-safe error handling *)
type error = [
  | `Input_error of string
  | `Auth_error of string
  | `Rate_limit_error of string
  | `Job_timeout_error of string
  | `Job_failed_error of string
  | `Wayback_error of string
]

let base_url = "https://web.archive.org"
let env_proxy_prefix = "PKIT_WB_PROXY_PREFIX"
let env_access_key = "INTERNET_ARCHIVE_ACCESS_KEY"
let env_secret_key = "INTERNET_ARCHIVE_SECRET_KEY"

let make_url path =
  match Sys.getenv_opt env_proxy_prefix with
  | Some prefix -> prefix ^ base_url ^ path
  | None -> base_url ^ path

(* Standard URL encoding for application/x-www-form-urlencoded POST bodies *)
let url_encode s =
  let buf = Buffer.create (String.length s * 2) in
  String.iter (fun c ->
    match c with
    | 'A'..'Z' | 'a'..'z' | '0'..'9' | '-' | '_' | '.' | '~' -> Buffer.add_char buf c
    | ' ' -> Buffer.add_char buf '+'
    | _ -> Buffer.add_string buf (Printf.sprintf "%%%02X" (Char.code c))
  ) s;
  Buffer.contents buf

let encode_params params =
  params
  |> List.map (fun (k, v) -> Printf.sprintf "%s=%s" (url_encode k) (url_encode v))
  |> String.concat "&"

let parse_json_exn s = Yojson.Safe.from_string s

let request ?(headers=[]) method_type path =
  let url = make_url path in
  let res = match method_type with
    | `GET -> Ezcurl.get ~headers ~url ()
    | `POST params ->
      let body = encode_params params in
      let headers = ("Content-Type", "application/x-www-form-urlencoded") :: headers in
      (* Ezcurl.post requires ~params. We pass an empty list and use ~content 
         to send the raw URL-encoded string body, avoiding multipart form quirks. *)
      Ezcurl.post ~headers ~params:[] ~content:(`String body) ~url ()
  in
  match res with
  | Error (_, msg) -> Error (`Wayback_error msg)
  | Ok resp ->
    (* Explicit module qualification prevents "Unbound record field" errors *)
    let code = resp.Ezcurl.code in
    let body = resp.Ezcurl.body in
    if code = 401 || code = 403 then Error (`Auth_error (Printf.sprintf "HTTP %d: %s" code body))
    else if code = 429 then Error (`Rate_limit_error (Printf.sprintf "HTTP %d: %s" code body))
    else if code >= 400 then Error (`Wayback_error (Printf.sprintf "HTTP %d: %s" code body))
    else
      try Ok (parse_json_exn body)
      with _ -> Ok (`Assoc [ ("raw_response", `String body) ])

let with_lock lock_file f =
  if lock_file = "" then f ()
  else
    let fd = Unix.openfile lock_file [Unix.O_CREAT; Unix.O_RDWR] 0o666 in
    let finally () = Unix.close fd in
    Fun.protect ~finally (fun () ->
      Unix.lockf fd Unix.F_LOCK 0;
      let res = f () in
      Unix.lockf fd Unix.F_ULOCK 0;
      res
    )

type t = {
  no_auth: bool;
  lock_file: string;
  timeout: float;
  headers: (string * string) list;
}

let make ?(no_auth=false) ?(lock_file="/tmp/spn2_submit.lock") ?(timeout=120.0) () =
  let headers =
    if no_auth then ["Accept", "application/json"]
    else
      match Sys.getenv_opt env_access_key, Sys.getenv_opt env_secret_key with
      | Some k, Some s -> ["Accept", "application/json"; "Authorization", Printf.sprintf "LOW %s:%s" k s]
      | _ -> ["Accept", "application/json"]
  in
  { no_auth; lock_file; timeout; headers }

let wait_for_availability t =
  let deadline = Unix.gettimeofday () +. 300.0 in
  let rec loop () =
    if Unix.gettimeofday () >= deadline then Error (`Job_timeout_error "Timeout waiting for queue")
    else
      match request ~headers:t.headers `GET "/save/status/user" with
      | Ok json ->
        let available = Yojson.Safe.Util.(json |> member "available" |> to_int_option) |> Option.value ~default:0 in
        if available >= 1 then Ok ()
        else (Unix.sleepf 10.0; loop ())
      | Error _ -> Ok () (* Proceed and let the actual save endpoint report the real error *)
  in loop ()

let submit_save_job t url opts =
  with_lock t.lock_file (fun () ->
    match wait_for_availability t with
    | Error e -> Error e
    | Ok () ->
      let payload = ("url", `String url) :: (Types.save_options_to_yojson opts |> Yojson.Safe.Util.to_assoc) in
      let params = List.map (fun (k, v) -> (k, Yojson.Safe.Util.to_string v)) payload in
      match request ~headers:t.headers (`POST params) "/save" with
      | Ok json ->
        (match Yojson.Safe.Util.(json |> member "job_id" |> to_string_option) with
         | Some id -> Unix.sleepf 1.0; Ok id
         | None -> Error (`Wayback_error "Missing job_id in SPN2 response"))
      | Error e -> Error e
  )

let poll_save_job t url job_id =
  let deadline = Unix.gettimeofday () +. 180.0 in
  let rec loop () =
    if Unix.gettimeofday () >= deadline then Error (`Job_timeout_error (Printf.sprintf "Timeout polling job %s" job_id))
    else
      match request ~headers:t.headers `GET (Printf.sprintf "/save/status/%s" job_id) with
      | Ok json ->
        let status = Yojson.Safe.Util.(json |> member "status" |> to_string) in
        if status = "success" then
          let timestamp = Yojson.Safe.Util.(json |> member "timestamp" |> to_string_option) |> Option.value ~default:"" in
          let original_url = Yojson.Safe.Util.(json |> member "original_url" |> to_string_option) |> Option.value ~default:url in
          Ok (Printf.sprintf "%s/web/%sid_/%s" base_url timestamp original_url)
        else if status = "pending" then (Unix.sleepf 15.0; loop ())
        else
          let detail = Yojson.Safe.Util.(json |> member "error" |> to_string_option) |> Option.value ~default:"unknown status" in
          Error (`Job_failed_error (Printf.sprintf "SPN2 job %s failed: %s" job_id detail))
      | Error e -> Error e
  in loop ()

let save_url t url ~opts =
  if url = "" then Error (`Input_error "No target URL provided.")
  else
    match submit_save_job t url opts with
    | Error e -> Error e
    | Ok job_id ->
      match poll_save_job t url job_id with
      | Ok archive_url -> Ok Types.{ url; archive_url; job_id }
      | Error e -> Error e
