"""Native sampling profiler for Windows x64 (ctypes + dbghelp), no admin rights.

Usage: python bench/winprof.py [--ms 1] [--top 40] [--lines] OUT.json -- PYTHON TARGET.py ARGS...

TARGET.py must print READY after its imports, wait for a line on stdin, run the
work, print DONE and wait for another line (see bench/winprof_target.py). The
profiler suspends the target's main thread about every --ms milliseconds,
walks its native stack with StackWalk64 and resolves symbols with dbghelp
(PDBs next to the DLL/PYD files). It prints self and inclusive time per
function, plus an ownership split: each sample is charged to the innermost
frame that is either SimPy library code or the CPython bytecode interpreter
(Python code such as the model's generators).

--lines also charges each sample to a source line of SimPy's C code: the
line of the innermost SimPy frame (for a call into CPython, the line of that
call), printed with the .pyx line the C code was generated from.
"""
import collections
import ctypes as C
import ctypes.wintypes as W
import json
import os
import subprocess
import sys
import time

k32 = C.WinDLL('kernel32', use_last_error=True)
psapi = C.WinDLL('psapi', use_last_error=True)
dbg = C.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32', 'dbghelp.dll'),
               use_last_error=True)

k32.OpenThread.restype = W.HANDLE
k32.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.OpenProcess.restype = W.HANDLE
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.CreateToolhelp32Snapshot.restype = W.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k32.SuspendThread.argtypes = [W.HANDLE]
k32.SuspendThread.restype = W.DWORD
k32.ResumeThread.argtypes = [W.HANDLE]
k32.ResumeThread.restype = W.DWORD
k32.GetThreadContext.argtypes = [W.HANDLE, C.c_void_p]
k32.GetThreadContext.restype = W.BOOL
dbg.SymInitializeW.argtypes = [W.HANDLE, W.LPCWSTR, W.BOOL]
dbg.SymInitializeW.restype = W.BOOL
dbg.SymSetOptions.argtypes = [W.DWORD]
dbg.SymFromAddr.argtypes = [W.HANDLE, C.c_uint64, C.POINTER(C.c_uint64), C.c_void_p]
dbg.SymFromAddr.restype = W.BOOL
dbg.SymGetLineFromAddrW64.argtypes = [W.HANDLE, C.c_uint64,
                                      C.POINTER(W.DWORD), C.c_void_p]
dbg.SymGetLineFromAddrW64.restype = W.BOOL
dbg.SymGetModuleBase64.argtypes = [W.HANDLE, C.c_uint64]
dbg.SymGetModuleBase64.restype = C.c_uint64
dbg.StackWalk64.argtypes = [W.DWORD, W.HANDLE, W.HANDLE, C.c_void_p, C.c_void_p,
                            C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
dbg.StackWalk64.restype = W.BOOL
psapi.EnumProcessModules.argtypes = [W.HANDLE, C.POINTER(W.HMODULE), W.DWORD,
                                     C.POINTER(W.DWORD)]
psapi.GetModuleBaseNameW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
psapi.GetModuleFileNameExW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]


class THREADENTRY32(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD),
                ('th32ThreadID', W.DWORD), ('th32OwnerProcessID', W.DWORD),
                ('tpBasePri', W.LONG), ('tpDeltaPri', W.LONG),
                ('dwFlags', W.DWORD)]


CTX_SIZE = 1232
CONTEXT_FULL = 0x10000B
FTA = C.cast(dbg.SymFunctionTableAccess64, C.c_void_p)
GMB = C.cast(dbg.SymGetModuleBase64, C.c_void_p)


def aligned(size):
    raw = C.create_string_buffer(size + 16)
    return raw, (C.addressof(raw) + 15) & ~15


def main_thread_id(pid):
    snap = k32.CreateToolhelp32Snapshot(4, 0)
    te = THREADENTRY32()
    te.dwSize = C.sizeof(te)
    ok = k32.Thread32First(snap, C.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            k32.CloseHandle(snap)
            return te.th32ThreadID
        ok = k32.Thread32Next(snap, C.byref(te))
    raise RuntimeError('no thread')


def module_names(hproc):
    mods = (W.HMODULE * 1024)()
    need = W.DWORD()
    psapi.EnumProcessModules(hproc, mods, C.sizeof(mods), C.byref(need))
    out, dirs = {}, []
    for i in range(need.value // C.sizeof(W.HMODULE)):
        buf = C.create_unicode_buffer(1024)
        psapi.GetModuleFileNameExW(hproc, mods[i], buf, 1024)
        out[mods[i] or 0] = os.path.basename(buf.value)
        d = os.path.dirname(buf.value)
        if d not in dirs:
            dirs.append(d)
    return out, dirs


def main():
    argv = sys.argv[1:]
    ms, top, lines = 1.0, 40, False
    while argv[0].startswith('--'):
        if argv[0] == '--lines':
            lines = True
            argv = argv[1:]
            continue
        if argv[0] == '--ms':
            ms = float(argv[1])
        elif argv[0] == '--top':
            top = int(argv[1])
        argv = argv[2:]
    out_path, sep, cmd = argv[0], argv[1], argv[2:]
    assert sep == '--'
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    line = proc.stdout.readline()
    assert line.strip() == 'READY', line
    hproc = k32.OpenProcess(0x0400 | 0x0010, False, proc.pid)
    hthr = k32.OpenThread(0x0002 | 0x0008 | 0x0040, False,
                          main_thread_id(proc.pid))
    dbg.SymSetOptions(0x2 | 0x4 | 0x10 | 0x200 | 0x80000)
    # The PDBs sit next to their DLL/PYD; dbghelp does not search there by
    # default, so the module folders form the symbol path.
    mods, dirs = module_names(hproc)
    assert dbg.SymInitializeW(hproc, ';'.join(dirs), True)

    ctx_raw, ctx = aligned(CTX_SIZE)
    walk_raw, walk = aligned(CTX_SIZE)
    sf = C.create_string_buffer(1024)
    sfa = C.addressof(sf)
    u64 = C.c_uint64.from_address
    samples = []
    proc.stdin.write('go\n')
    proc.stdin.flush()
    t0 = time.perf_counter()
    import threading
    done = threading.Event()

    def wait_done():
        proc.stdout.readline()
        done.set()

    threading.Thread(target=wait_done, daemon=True).start()
    while not done.is_set():
        if k32.SuspendThread(hthr) == 0xFFFFFFFF:
            break
        C.memset(ctx, 0, CTX_SIZE)
        C.c_uint32.from_address(ctx + 0x30).value = CONTEXT_FULL
        if k32.GetThreadContext(hthr, ctx):
            C.memmove(walk, ctx, CTX_SIZE)
            C.memset(sfa, 0, 1024)
            rip, rsp = u64(ctx + 0xF8).value, u64(ctx + 0x98).value
            for off, val in ((0, rip), (32, rsp), (48, rsp)):
                u64(sfa + off).value = val
                C.c_uint32.from_address(sfa + off + 12).value = 3
            frames = []
            while len(frames) < 96 and dbg.StackWalk64(
                    0x8664, hproc, hthr, sfa, walk, None, FTA, GMB, None):
                pc = u64(sfa).value
                if not pc:
                    break
                frames.append(pc)
            samples.append(frames or [rip])
        k32.ResumeThread(hthr)
        time.sleep(ms / 1000)
    wall = time.perf_counter() - t0

    sym_raw = C.create_string_buffer(88 + 512)
    syma = C.addressof(sym_raw)
    names = {}

    def name(pc):
        n = names.get(pc)
        if n is None:
            # Return addresses point after the call; look up pc-1.
            C.memset(syma, 0, 88)
            C.c_uint32.from_address(syma).value = 88
            C.c_uint32.from_address(syma + 80).value = 500
            disp = C.c_uint64()
            base = dbg.SymGetModuleBase64(hproc, pc)
            mod = mods.get(base, '?').rsplit('.', 1)[0].split('.')[0]
            if dbg.SymFromAddr(hproc, pc - 1, C.byref(disp), syma):
                fn = C.string_at(syma + 84).decode(errors='replace')
            else:
                fn = f'0x{pc - base:x}' if base else f'0x{pc:x}'
            n = names[pc] = f'{mod}!{fn}'
        return n

    stacks = [[name(pc) for pc in s] for s in samples]
    line_of = {}

    def source_line(pc):
        key = line_of.get(pc)
        if key is None:
            info = C.create_string_buffer(40)
            C.c_uint32.from_address(C.addressof(info)).value = 40
            disp = W.DWORD()
            if dbg.SymGetLineFromAddrW64(hproc, pc, C.byref(disp), info):
                lineno = C.c_uint32.from_address(C.addressof(info) + 16).value
                fname = C.wstring_at(C.c_uint64.from_address(
                    C.addressof(info) + 24).value)
                key = (fname, lineno)
            else:
                key = ('?', 0)
            line_of[pc] = key
        return key

    sample_lines = []
    if lines:
        for pcs, s in zip(samples, stacks):
            for i, f in enumerate(s):
                if f.startswith(SIMPY_MODS):
                    # Frames above the leaf hold return addresses.
                    sample_lines.append(source_line(pcs[i] - (i > 0)))
                    break
            else:
                sample_lines.append(None)
    proc.stdin.write('bye\n')
    proc.stdin.flush()
    proc.wait()

    n = len(stacks)
    self_c = collections.Counter(s[0] for s in stacks)
    incl_c = collections.Counter()
    for s in stacks:
        for f in set(s):
            incl_c[f] += 1
    owner_c = collections.Counter()
    for s in stacks:
        owner = 'other'
        for f in s:
            if f.startswith(SIMPY_MODS):
                owner = 'simpy-lib'
                break
            if f.endswith('!_PyEval_EvalFrameDefault'):
                owner = 'python-bytecode'
                break
        owner_c[owner] += 1
    print(f'samples={n} wall={wall:.2f}s interval={ms}ms')
    print('\n== ownership (innermost of simpy C code vs bytecode interpreter) ==')
    for k, v in owner_c.most_common():
        print(f'{100 * v / n:6.1f}%  {k}')
    print(f'\n== self (top {top}) ==')
    for k, v in self_c.most_common(top):
        print(f'{100 * v / n:6.1f}%  {k}')
    print(f'\n== inclusive (top {top}) ==')
    for k, v in incl_c.most_common(top):
        print(f'{100 * v / n:6.1f}%  {k}')
    if lines:
        print(f'\n== source lines of SimPy C code (top {top}) ==')
        line_c = collections.Counter(k for k in sample_lines if k)
        for (fname, lineno), v in line_c.most_common(top):
            print(f'{100 * v / n:6.1f}%  {os.path.basename(fname)}:{lineno}'
                  f'  {pyx_ref(fname, lineno)}')
    with open(out_path, 'w') as f:
        json.dump({'wall': wall, 'stacks': stacks,
                   'lines': [list(k) if k else None for k in sample_lines]}, f)


_c_cache = {}


def pyx_ref(fname, lineno):
    """The C line and the .pyx line Cython generated it from (the nearest
    preceding '/* "file.pyx":N' comment)."""
    text = _c_cache.get(fname)
    if text is None:
        try:
            with open(fname, encoding='utf-8', errors='replace') as f:
                text = f.read().splitlines()
        except OSError:
            text = []
        _c_cache[fname] = text
    if not 0 < lineno <= len(text):
        return ''
    code = text[lineno - 1].strip()[:70]
    for i in range(lineno - 1, max(lineno - 400, 0), -1):
        t = text[i - 1].strip()
        if t.startswith('/* "') and '.pyx":' in t:
            return f'[{t[3:].split(" ")[0].strip(chr(34))}] {code}'
    return code


SIMPY_MODS = ('core!', 'events!', 'base!', 'resource!', 'store!', 'container!')


if __name__ == '__main__':
    main()
