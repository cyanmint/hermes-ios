use anyhow::{Context, Result};
use std::{env, path::PathBuf};
use wasmtime::{Caller, Engine, Linker, Module, Store};
use wasmtime_wasi::{p1::WasiP1Ctx, FsPerms, WasiCtxBuilder};

fn guest_bytes(caller: &mut Caller<'_, WasiP1Ctx>, ptr: i32, len: i32) -> Option<Vec<u8>> {
    let memory = caller.get_export("memory")?.into_memory()?;
    let mut bytes = vec![0; len.max(0) as usize];
    memory.read(caller, ptr.max(0) as usize, &mut bytes).ok()?;
    Some(bytes)
}

fn main() -> Result<()> {
    let mut args = env::args_os();
    let _runner = args.next();
    let wasm = PathBuf::from(args.next().context("usage: ashell-wasmtime-stub MODULE [ARGS...] ")?);
    let host_root = env::current_dir()?;
    let guest_args: Vec<String> = std::iter::once(wasm.as_os_str().to_string_lossy().into_owned())
        .chain(args.map(|arg| arg.to_string_lossy().into_owned()))
        .collect();

    let mut config = wasmtime::Config::new();
    config.wasm_multi_memory(true);
    let engine = Engine::new(&config)?;
    let module = Module::from_file(&engine, &wasm)?;
    let mut linker = Linker::new(&engine);
    wasmtime_wasi::p1::add_to_linker_sync(&mut linker, |ctx: &mut WasiP1Ctx| ctx)?;

    linker.func_wrap("wasi_snapshot_preview1", "ashell_system", |mut caller: Caller<'_, WasiP1Ctx>, ptr: i32, len: i32| -> i32 {
        let command = guest_bytes(&mut caller, ptr, len).unwrap_or_default();
        eprintln!("ASHELL_STUB ashell_system len={} bytes={:?}", command.len(), command);
        0
    })?;
    linker.func_wrap("wasi_snapshot_preview1", "ashell_chdir", |mut caller: Caller<'_, WasiP1Ctx>, ptr: i32, len: i32| -> i32 {
        let path = guest_bytes(&mut caller, ptr, len).unwrap_or_default();
        eprintln!("ASHELL_STUB ashell_chdir path={:?}", String::from_utf8_lossy(&path));
        0
    })?;
    linker.func_wrap("wasi_snapshot_preview1", "ashell_fchdir", |_caller: Caller<'_, WasiP1Ctx>, fd: i32| -> i32 {
        eprintln!("ASHELL_STUB ashell_fchdir fd={fd}");
        0
    })?;
    linker.func_wrap("wasi_snapshot_preview1", "ashell_getcwd", |mut caller: Caller<'_, WasiP1Ctx>, ptr: i32, len: i32, used_ptr: i32| -> i32 {
        let bytes = b".".to_vec();
        if let Some(memory) = caller.get_export("memory").and_then(|e| e.into_memory()) {
            let n = bytes.len().min(len.max(0) as usize);
            let _ = memory.write(&mut caller, ptr.max(0) as usize, &bytes[..n]);
            if n < len.max(0) as usize {
                let _ = memory.write(&mut caller, ptr.max(0) as usize + n, &[0]);
            }
            let _ = memory.write(&mut caller, used_ptr.max(0) as usize, &(n as u32).to_le_bytes());
            eprintln!("ASHELL_STUB ashell_getcwd wrote={n}");
        }
        0
    })?;

    let wasi = WasiCtxBuilder::new()
        .inherit_stdio()
        .env("PYTHONHOME", "/")
        .env("PYTHONPATH", ".:./hermesrt.zip:hermesrt.zip:/hermesrt.zip")
        .args(&guest_args)
        .initial_cwd(".")
        .preopened_dir(&host_root, ".", FsPerms::ReadWrite)?
        .build_p1();
    let mut store = Store::new(&engine, wasi);
    let instance = linker.instantiate(&mut store, &module)?;
    let start = instance.get_typed_func::<(), ()>(&mut store, "_start")?;
    start.call(&mut store, ())?;
    Ok(())
}
