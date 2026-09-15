; The smallest Win32 program that shuts the machine down: ExitWindowsEx with the shutdown
; privilege enabled, nothing else.
;
; Windows NT 4.0 ships no way to do this from a script: shutdown.exe is a Resource Kit tool,
; there is no WMI or Windows Script Host in the box, and RUNDLL32 cannot pass ExitWindowsEx
; the flags it needs. So the first-logon script of the unattended install carries this file
; (VMCTLOFF.EXE) and runs it after the completion token. NT 4 cannot power the machine off
; (no APM, no ACPI): with EWX_POWEROFF added it rebooted instead (verified live), so the flags
; are shutdown + force only, and the guest stops at "It is now safe to turn off your computer"
; with everything flushed; the host closes QEMU after its grace period.
;
; Hand-written PE32: one section holding code, data and the import tables, subsystem version
; 4.0 so the NT 4 loader accepts it. Exit code 0 when ExitWindowsEx succeeded, 1 otherwise.
;
; Assemble with:  nasm -f bin exitwin.asm -o exitwin.exe   (the bytes live in vmctl/windowsnt4.py)

bits 32

IMAGE_BASE  equ 0x400000
SECT_RVA    equ 0x1000
%define RVA(x) ((x) - sect + SECT_RVA)
%define VA(x)  (RVA(x) + IMAGE_BASE)

; ---------------------------------------------------------------- DOS header
db 'MZ'
times 0x3C - ($ - $$) db 0
dd pe_header                    ; e_lfanew

; ---------------------------------------------------------------- PE headers
pe_header:
db 'PE', 0, 0
dw 0x014C                       ; Machine: i386
dw 1                            ; NumberOfSections
dd 0                            ; TimeDateStamp
dd 0                            ; PointerToSymbolTable
dd 0                            ; NumberOfSymbols
dw optional_end - optional      ; SizeOfOptionalHeader
dw 0x0103                       ; RELOCS_STRIPPED | EXECUTABLE_IMAGE | 32BIT_MACHINE

optional:
dw 0x010B                       ; PE32
db 1, 0                         ; linker version
dd sect_raw_size                ; SizeOfCode
dd 0                            ; SizeOfInitializedData
dd 0                            ; SizeOfUninitializedData
dd RVA(entry)                   ; AddressOfEntryPoint
dd SECT_RVA                     ; BaseOfCode
dd SECT_RVA                     ; BaseOfData
dd IMAGE_BASE                   ; ImageBase
dd 0x1000                       ; SectionAlignment
dd 0x200                        ; FileAlignment
dw 4, 0                         ; operating system version 4.0
dw 0, 0                         ; image version
dw 4, 0                         ; subsystem version 4.0 (NT 4 refuses anything newer)
dd 0                            ; Win32VersionValue
dd SECT_RVA + 0x1000            ; SizeOfImage: the headers page and one section page
dd 0x200                        ; SizeOfHeaders
dd 0                            ; CheckSum
dw 3                            ; Subsystem: console (inherits cmd's, no window of its own)
dw 0                            ; DllCharacteristics
dd 0x100000, 0x1000             ; stack reserve, commit
dd 0x100000, 0x1000             ; heap reserve, commit
dd 0                            ; LoaderFlags
dd 16                           ; NumberOfRvaAndSizes
dd 0, 0                                             ; export table
dd RVA(import_dir), import_dir_end - import_dir     ; import table
times 14 dd 0, 0                                    ; the other directories
optional_end:

; section header
db '.text', 0, 0, 0
dd sect_end - sect              ; VirtualSize
dd SECT_RVA                     ; VirtualAddress
dd sect_raw_size                ; SizeOfRawData
dd 0x200                        ; PointerToRawData
dd 0, 0                         ; relocations, line numbers
dw 0, 0
dd 0xE0000060                   ; CODE | INITIALIZED_DATA | EXECUTE | READ | WRITE

times 0x200 - ($ - $$) db 0

; ---------------------------------------------------------------- the section
sect:
entry:
    ; OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)
    push VA(token)
    push 0x28
    call [VA(iat_GetCurrentProcess)]
    push eax
    call [VA(iat_OpenProcessToken)]
    ; LookupPrivilegeValueA(NULL, "SeShutdownPrivilege", &privileges.Privileges[0].Luid)
    push VA(luid)
    push VA(privilege_name)
    push 0
    call [VA(iat_LookupPrivilegeValueA)]
    ; AdjustTokenPrivileges(token, FALSE, &privileges, 0, NULL, NULL)
    push 0
    push 0
    push 0
    push VA(privileges)
    push 0
    push dword [VA(token)]
    call [VA(iat_AdjustTokenPrivileges)]
    ; ExitWindowsEx(EWX_SHUTDOWN | EWX_FORCE, 0)
    push 0
    push 0x05
    call [VA(iat_ExitWindowsEx)]
    ; ExitProcess(0 if ExitWindowsEx returned TRUE, else 1)
    test eax, eax
    setz al
    movzx eax, al
    push eax
    call [VA(iat_ExitProcess)]

align 4, db 0
token:          dd 0
privileges:     dd 1            ; PrivilegeCount
luid:           dd 0, 0         ; LUID, filled in by LookupPrivilegeValue
                dd 2            ; SE_PRIVILEGE_ENABLED
privilege_name: db 'SeShutdownPrivilege', 0

; ---------------------------------------------------------------- imports
align 4, db 0
import_dir:
dd RVA(ilt_kernel32), 0, 0, RVA(name_kernel32), RVA(iat_kernel32)
dd RVA(ilt_advapi32), 0, 0, RVA(name_advapi32), RVA(iat_advapi32)
dd RVA(ilt_user32),   0, 0, RVA(name_user32),   RVA(iat_user32)
dd 0, 0, 0, 0, 0
import_dir_end:

ilt_kernel32:               dd RVA(hint_GetCurrentProcess), RVA(hint_ExitProcess), 0
iat_kernel32:
iat_GetCurrentProcess:      dd RVA(hint_GetCurrentProcess)
iat_ExitProcess:            dd RVA(hint_ExitProcess)
                            dd 0
ilt_advapi32:               dd RVA(hint_OpenProcessToken), RVA(hint_LookupPrivilegeValueA), RVA(hint_AdjustTokenPrivileges), 0
iat_advapi32:
iat_OpenProcessToken:       dd RVA(hint_OpenProcessToken)
iat_LookupPrivilegeValueA:  dd RVA(hint_LookupPrivilegeValueA)
iat_AdjustTokenPrivileges:  dd RVA(hint_AdjustTokenPrivileges)
                            dd 0
ilt_user32:                 dd RVA(hint_ExitWindowsEx), 0
iat_user32:
iat_ExitWindowsEx:          dd RVA(hint_ExitWindowsEx)
                            dd 0

align 2, db 0
hint_GetCurrentProcess:     dw 0
                            db 'GetCurrentProcess', 0
align 2, db 0
hint_ExitProcess:           dw 0
                            db 'ExitProcess', 0
align 2, db 0
hint_OpenProcessToken:      dw 0
                            db 'OpenProcessToken', 0
align 2, db 0
hint_LookupPrivilegeValueA: dw 0
                            db 'LookupPrivilegeValueA', 0
align 2, db 0
hint_AdjustTokenPrivileges: dw 0
                            db 'AdjustTokenPrivileges', 0
align 2, db 0
hint_ExitWindowsEx:         dw 0
                            db 'ExitWindowsEx', 0

name_kernel32:  db 'KERNEL32.DLL', 0
name_advapi32:  db 'ADVAPI32.DLL', 0
name_user32:    db 'USER32.DLL', 0
sect_end:

times (0x200 - (($ - sect) % 0x200)) % 0x200 db 0
sect_raw_size equ $ - sect
