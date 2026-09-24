module Cli_helpers = struct
  let fail msg ~exit_code =
    Printf.eprintf "Error: %s\n" msg;
    exit exit_code

  let read_stdin_payload json_mode =
    if Unix.isatty Unix.stdin then None, `Assoc []
    else
      let buf = Buffer.create 1024 in
      (try while true do Buffer.add_channel buf stdin 1 done with End_of_file -> ());
      let raw = String.trim (Buffer.contents buf) in
      if raw = "" then None, `Assoc []
      else if not json_mode then
        let first_line = List.hd (String.split_on_char '\n' raw) in
        Some (String.trim first_line), `Assoc []
      else
        try
          let json = Yojson.Safe.from_string raw in
          match json with
          | `Assoc _ as obj ->
            let url = match Yojson.Safe.Util.member "url" obj with
              | `String s -> Some s
              | `Int i -> Some (string_of_int i)
              | _ -> None
            in
            url, obj
          | `String s -> Some s, `Assoc []
          | _ -> Some raw, `Assoc []
        with _ -> Some raw, `Assoc []
end
