"""Assemble the project's authored AIR fixtures using the bundled LLVM C API.

Used by optional device validation builds; no external shader compiler, process
replacement, or synthetic entry point is introduced. The LLVM parser owns its
input buffer after parsing, and all contexts/modules/messages are released.
"""
import ctypes as c
import os
from pathlib import Path


def assemble(source: Path, target: Path, runtime: Path):
    with os.add_dll_directory(str(runtime)):
        llvm = c.CDLL(str(runtime / 'libLLVM-23.dll'))
    llvm.LLVMContextCreate.restype = c.c_void_p
    llvm.LLVMCreateMemoryBufferWithContentsOfFile.argtypes = [c.c_char_p, c.POINTER(c.c_void_p), c.POINTER(c.c_char_p)]
    llvm.LLVMParseIRInContext.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p), c.POINTER(c.c_char_p)]
    llvm.LLVMWriteBitcodeToFile.argtypes = [c.c_void_p, c.c_char_p]
    llvm.LLVMDisposeModule.argtypes = [c.c_void_p]
    llvm.LLVMContextDispose.argtypes = [c.c_void_p]
    llvm.LLVMDisposeMessage.argtypes = [c.c_char_p]
    context, module, buffer, error = llvm.LLVMContextCreate(), c.c_void_p(), c.c_void_p(), c.c_char_p()
    if not context:
        raise MemoryError('LLVM context allocation failed')
    try:
        if llvm.LLVMCreateMemoryBufferWithContentsOfFile(str(source).encode(), c.byref(buffer), c.byref(error)):
            raise RuntimeError(error.value.decode('utf-8', 'replace') if error.value else 'LLVM could not read AIR')
        if llvm.LLVMParseIRInContext(context, buffer, c.byref(module), c.byref(error)):
            raise RuntimeError(error.value.decode('utf-8', 'replace') if error.value else 'LLVM could not parse AIR')
        if llvm.LLVMWriteBitcodeToFile(module, str(target).encode()):
            raise OSError('LLVM could not write AIR bitcode')
    finally:
        if error.value:
            llvm.LLVMDisposeMessage(error)
        if module:
            llvm.LLVMDisposeModule(module)
        llvm.LLVMContextDispose(context)
