#define UNICODE
#define _UNICODE

#include <stdint.h>
#include <stdlib.h>
#include <wchar.h>
#include <windows.h>
#include <shellapi.h>

static void show_windows_error(const wchar_t *message, DWORD error_code) {
    wchar_t *system_message = NULL;
    FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        NULL, error_code, 0, (wchar_t *)&system_message, 0, NULL
    );

    size_t message_length = wcslen(message);
    size_t system_length = system_message ? wcslen(system_message) : 0;
    wchar_t *text = calloc(message_length + system_length + 32, sizeof(wchar_t));
    if (text) {
        swprintf_s(text, message_length + system_length + 32, L"%ls\n\n%lsError code: %lu", message,
                   system_message ? system_message : L"", error_code);
        MessageBoxW(NULL, text, L"MochiStar", MB_OK | MB_ICONERROR);
        free(text);
    } else {
        MessageBoxW(NULL, message, L"MochiStar", MB_OK | MB_ICONERROR);
    }
    if (system_message) LocalFree(system_message);
}

static wchar_t *launcher_path(void) {
    DWORD capacity = 260;
    while (capacity <= 32768) {
        wchar_t *path = calloc(capacity, sizeof(wchar_t));
        if (!path) {
            SetLastError(ERROR_OUTOFMEMORY);
            return NULL;
        }
        DWORD length = GetModuleFileNameW(NULL, path, capacity);
        if (length > 0 && length < capacity) return path;
        free(path);
        if (length == 0) return NULL;
        capacity *= 2;
    }
    SetLastError(ERROR_FILENAME_EXCED_RANGE);
    return NULL;
}

static wchar_t *application_path(void) {
    wchar_t *path = launcher_path();
    if (!path) return NULL;
    wchar_t *separator = wcsrchr(path, L'\\');
    if (!separator) {
        free(path);
        SetLastError(ERROR_BAD_PATHNAME);
        return NULL;
    }

    static const wchar_t suffix[] = L"\\app\\MochiStar.exe";
    size_t directory_length = (size_t)(separator - path);
    wchar_t *application = calloc(directory_length + _countof(suffix), sizeof(wchar_t));
    if (!application) {
        free(path);
        SetLastError(ERROR_OUTOFMEMORY);
        return NULL;
    }
    wmemcpy(application, path, directory_length);
    wcscpy_s(application + directory_length, _countof(suffix), suffix);
    free(path);
    return application;
}

static size_t quoted_argument_length(const wchar_t *argument) {
    size_t length = 2;
    size_t backslashes = 0;
    for (const wchar_t *cursor = argument; *cursor; ++cursor) {
        if (*cursor == L'\\') {
            ++backslashes;
        } else if (*cursor == L'"') {
            length += backslashes * 2 + 2;
            backslashes = 0;
        } else {
            length += backslashes + 1;
            backslashes = 0;
        }
    }
    return length + backslashes * 2;
}

static wchar_t *append_quoted_argument(wchar_t *output, const wchar_t *argument) {
    *output++ = L'"';
    size_t backslashes = 0;
    for (const wchar_t *cursor = argument; *cursor; ++cursor) {
        if (*cursor == L'\\') {
            ++backslashes;
            continue;
        }
        size_t count = *cursor == L'"' ? backslashes * 2 + 1 : backslashes;
        while (count--) *output++ = L'\\';
        *output++ = *cursor;
        backslashes = 0;
    }
    for (size_t count = backslashes * 2; count; --count) *output++ = L'\\';
    *output++ = L'"';
    return output;
}

static wchar_t *build_command_line(const wchar_t *application) {
    int argument_count = 0;
    wchar_t **arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    if (!arguments) return NULL;

    size_t length = quoted_argument_length(application);
    for (int index = 1; index < argument_count; ++index) length += 1 + quoted_argument_length(arguments[index]);
    wchar_t *command_line = calloc(length + 1, sizeof(wchar_t));
    if (!command_line) {
        LocalFree(arguments);
        SetLastError(ERROR_OUTOFMEMORY);
        return NULL;
    }

    wchar_t *output = append_quoted_argument(command_line, application);
    for (int index = 1; index < argument_count; ++index) {
        *output++ = L' ';
        output = append_quoted_argument(output, arguments[index]);
    }
    *output = L'\0';
    LocalFree(arguments);
    return command_line;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous_instance, PWSTR command_line_argument, int show_command) {
    (void)instance;
    (void)previous_instance;
    (void)command_line_argument;
    (void)show_command;

    wchar_t *application = application_path();
    if (!application) {
        show_windows_error(L"Unable to locate the MochiStar application directory", GetLastError());
        return 1;
    }
    if (GetFileAttributesW(application) == INVALID_FILE_ATTRIBUTES) {
        show_windows_error(L"app\\MochiStar.exe is missing or cannot be accessed", GetLastError());
        free(application);
        return 1;
    }

    wchar_t *command_line = build_command_line(application);
    if (!command_line) {
        show_windows_error(L"Unable to prepare the MochiStar command line", GetLastError());
        free(application);
        return 1;
    }

    STARTUPINFOW startup = {.cb = sizeof(startup)};
    PROCESS_INFORMATION process = {0};
    BOOL started = CreateProcessW(application, command_line, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process);
    free(command_line);
    free(application);
    if (!started) {
        show_windows_error(L"Unable to start app\\MochiStar.exe", GetLastError());
        return 1;
    }

    CloseHandle(process.hThread);
    if (WaitForSingleObject(process.hProcess, INFINITE) == WAIT_FAILED) {
        show_windows_error(L"Unable to wait for MochiStar to exit", GetLastError());
        CloseHandle(process.hProcess);
        return 1;
    }

    DWORD exit_code = 1;
    if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
        show_windows_error(L"Unable to read the MochiStar exit code", GetLastError());
        exit_code = 1;
    }
    CloseHandle(process.hProcess);
    return (int)exit_code;
}
