open Cmdliner

let main_cmd =
  let doc = "BusyBox-style personal CLI toolkit." in
  let info = Cmd.info "pkit" ~doc ~version:"0.2.0" in
  Cmd.group info [ Pkit_wayback.Cli.cmd ]

let () =
  let prog = Filename.basename Sys.argv.(0) in
  (* BusyBox mode: if invoked via symlink as 'wb' or 'wayback', 
     inject 'wb' as argv[1] so Cmdliner group parsing works uniformly *)
  let argv =
    if prog = "wb" || prog = "wayback" || prog = "pkit-wb" then
      Array.concat [ [| Sys.argv.(0); "wb" |]; Array.sub Sys.argv 1 (Array.length Sys.argv - 1) ]
    else
      Sys.argv
  in
  exit (Cmd.eval ~argv main_cmd)
