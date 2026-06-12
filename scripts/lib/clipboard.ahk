; ===========================================================================
; clipboard.ahk — shared selection/clipboard capture for hotkey actions.
; ===========================================================================

; Returns true when text was captured. Sets capturedText and captureSource
; ("selection" or "clipboard"). Restores the user's clipboard on all paths —
; including exceptions mid-capture — and retries the restore briefly because
; another app can hold the clipboard open at that exact moment.
CaptureTextFromSelectionOrClipboard(&capturedText, &captureSource) {
    priorClip := ""
    try priorClip := A_Clipboard
    catch
        priorClip := ""

    clipSaved := ""
    try {
        clipSaved := ClipboardAll()
        A_Clipboard := ""
    } catch {
        capturedText := ""
        captureSource := "clipboard_busy"
        return false
    }

    fromSelection := ""
    try {
        Send("^c")
        if ClipWait(1) {
            try
                fromSelection := A_Clipboard
            catch
                fromSelection := ""
        }
    } finally {
        RestoreClipboard(clipSaved)
    }

    capturedText := ""
    captureSource := ""
    if (fromSelection != "") {
        capturedText := fromSelection
        captureSource := "selection"
    } else if (priorClip != "") {
        capturedText := priorClip
        captureSource := "clipboard"
    }
    return (capturedText != "")
}

; Put the saved clipboard back, retrying a few times — a clipboard manager,
; RDP session, or app mid-copy can hold the clipboard and make a single
; attempt fail silently (the user would lose whatever they had copied).
RestoreClipboard(clipSaved) {
    loop 3 {
        try {
            A_Clipboard := clipSaved
            return true
        } catch {
            Sleep(60)
        }
    }
    return false
}
