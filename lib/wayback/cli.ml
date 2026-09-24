open Cmdliner

let url_arg =
  let doc = "The URL to save." in
  Arg.(value & pos 0 (some string) None & info [] ~docv:"URL" ~doc)

let json_flag = Arg.(value & flag & info ["json"] ~doc:"Output JSON.")
let capture_all_flag = Arg.(value & flag & info ["capture-all"] ~doc:"Capture all embedded resources.")
let no_auth_flag = Arg.(value & flag & info ["no-auth"] ~doc:"Skip sending S3 authentication headers.")
let lock_file_opt = Arg.(value & opt string "/tmp/spn2_submit.lock" & info ["lock-file"] ~docv:"PATH" ~doc:"Lock file path. Empty string disables locking.")

let save_cmd =
  let run url json_output capture_all no_auth lock_file =
    let opts = Types.{
      capture_all; capture_outlinks = false; email_result = true;
      force_get = true; skip_first_archive = true;
    } in
    let url = match url with
      | Some u -> u
      | None ->
        match Pkit_core.Cli_helpers.read_stdin_payload json_output with
        | Some u, _ -> u
        | None, _ -> Pkit_core.Cli_helpers.fail "No target URL provided." ~exit_code:2
    in
    let client = Client.make ~no_auth ~lock_file () in
    match Client.save_url client url ~opts with
    | Ok res ->
      if json_output then Printf.printf "%s\n" (Yojson.Safe.pretty_to_string (Types.save_result_to_yojson res))
      else Printf.printf "%s\n" res.archive_url
    | Error (`Input_error msg) -> Pkit_core.Cli_helpers.fail msg ~exit_code:2
    | Error (`Auth_error msg) -> Pkit_core.Cli_helpers.fail msg ~exit_code:2
    | Error (`Rate_limit_error msg) -> Pkit_core.Cli_helpers.fail msg ~exit_code:75
    | Error (`Job_timeout_error msg) -> Pkit_core.Cli_helpers.fail msg ~exit_code:75
    | Error (`Job_failed_error msg) -> Pkit_core.Cli_helpers.fail msg ~exit_code:1
    | Error (`Wayback_error msg) -> Pkit_core.Cli_helpers.fail msg ~exit_code:1
  in
  let doc = "Save one URL to the Wayback Machine using SPN2." in
  let info = Cmd.info "save" ~doc in
  Cmd.v info Term.(const run $ url_arg $ json_flag $ capture_all_flag $ no_auth_flag $ lock_file_opt)

let cmd =
  let doc = "Wayback Machine commands." in
  let info = Cmd.info "wb" ~doc ~version:"0.2.0" in
  Cmd.group info [ save_cmd ]
