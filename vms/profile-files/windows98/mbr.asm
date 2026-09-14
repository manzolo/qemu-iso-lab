; Master boot record for a lab disk that is not bootable yet.
;
; Windows 98 Setup does not partition or format: the host prepares the disk with a partition
; table and a FAT32 filesystem, so sector 0 needs boot code of its own. It must do two things,
; and the second one is why the stock "jump into zeros" does not work: while the guest is still
; being installed the partition holds no boot sector, and the BIOS has to be told to move on to
; the CD instead of hanging on "Booting from Hard Disk...".
;
;   1. chainload the active partition when its volume boot record is bootable (after Setup has
;      run SYS on C:, which is what makes the installed system start);
;   2. otherwise INT 18h, which returns to the BIOS boot order - the installer CD comes next.
;
; Assemble with:  nasm -f bin mbr.asm -o mbr.bin   (440 bytes max: the partition table follows)

bits 16
org 0x600

start:
    xor ax, ax
    mov ss, ax
    mov sp, 0x7C00
    mov ds, ax
    mov es, ax
    cld
    ; The volume boot record is loaded where we are running, so move out of the way first.
    mov si, 0x7C00
    mov di, 0x600
    mov cx, 256
    rep movsw
    jmp 0x0000:relocated

relocated:
    mov bp, 0x600 + 446             ; the partition table, in its new home
    mov cx, 4
.scan:
    cmp byte [bp], 0x80             ; active flag
    je .found
    add bp, 16
    dec cx
    jnz .scan
    jmp fail                        ; nothing active yet: let the BIOS try the next device
.found:
    mov eax, [bp + 8]               ; first LBA of the partition
    mov [dap_lba], eax
    mov si, dap
    mov ah, 0x42                    ; INT 13h extensions; DL still holds the BIOS drive
    int 0x13
    jc fail
    cmp word [0x7DFE], 0xAA55       ; is there a boot sector on it?
    jne fail
    mov si, bp                      ; a volume boot record expects DS:SI on its own entry
    jmp 0x0000:0x7C00

fail:
    int 0x18
    jmp fail                        ; some BIOSes return from INT 18h

dap:
    db 0x10, 0x00
    dw 0x0001                       ; one sector
    dw 0x7C00, 0x0000               ; to 0000:7C00
dap_lba:
    dd 0x00000000, 0x00000000
