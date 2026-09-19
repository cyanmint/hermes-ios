#include <Python.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>

static char **build_argv(int argc, char **argv) {
    char **result = calloc((size_t)argc + 1, sizeof(*result));
    if (result == NULL) {
        return NULL;
    }
    for (int i = 0; i < argc; ++i) {
        result[i] = argv[i];
    }
    return result;
}

static int report_python_error(const char *stage) {
    fprintf(stderr, "hermes: %s failed (error=%d)\n", stage,
            PyErr_Occurred() != NULL);
    if (PyErr_Occurred()) {
        PyObject *type = NULL;
        PyObject *value = NULL;
        PyObject *traceback = NULL;
        PyErr_Fetch(&type, &value, &traceback);
        PyErr_NormalizeException(&type, &value, &traceback);
        PyObject *text = value == NULL ? NULL : PyObject_Str(value);
        const char *message = text == NULL ? "<unprintable>" : PyUnicode_AsUTF8(text);
        fprintf(stderr, "hermes: python error: %s\n",
                message == NULL ? "<non-utf8>" : message);
        if (type != NULL || value != NULL || traceback != NULL) {
            PyErr_Restore(type, value, traceback);
            type = value = traceback = NULL;
            PyErr_Print();
        }
        Py_XDECREF(text);
        Py_XDECREF(type);
        Py_XDECREF(value);
        Py_XDECREF(traceback);
    }
    fflush(stderr);
    return 1;
}

static int handle_system_exit(void) {
    if (!PyErr_ExceptionMatches(PyExc_SystemExit)) return -1;
    PyObject *type = NULL, *value = NULL, *traceback = NULL;
    PyErr_Fetch(&type, &value, &traceback);
    int result = (value != NULL && PyLong_Check(value)) ? (int)PyLong_AsLong(value) : 0;
    Py_XDECREF(type);
    Py_XDECREF(value);
    Py_XDECREF(traceback);
    return result;
}

static void flush_python_stdio(void) {
    PyRun_SimpleString("import sys; sys.stdout.flush(); sys.stderr.flush()\n");
    fflush(stdout);
    fflush(stderr);
}

static int configure_python_stdio(void) {
    int result = PyRun_SimpleString(
        "import io, os, sys\n"
        "sys.stdout = io.TextIOWrapper(os.fdopen(os.dup(1), 'wb'), encoding='utf-8', errors='backslashreplace', line_buffering=True)\n"
        "sys.stderr = io.TextIOWrapper(os.fdopen(os.dup(2), 'wb'), encoding='utf-8', errors='backslashreplace', line_buffering=True)\n");
    if (result != 0) {
        PyErr_Clear();
        return -1;
    }
    return 0;
}

PyMODINIT_FUNC PyInit_binascii(void);
PyMODINIT_FUNC PyInit__struct(void);
PyMODINIT_FUNC PyInit__socket(void);
PyMODINIT_FUNC PyInit_select(void);
PyMODINIT_FUNC PyInit_math(void);
PyMODINIT_FUNC PyInit_cmath(void);
PyMODINIT_FUNC PyInit__contextvars(void);

int main(int argc, char **argv) {
    const char *runtime_root = getenv("HERMES_RUNTIME_ROOT");
    char runtime_path[PATH_MAX];
    if (runtime_root == NULL || runtime_root[0] == '\0') runtime_root = ".";
    if (snprintf(runtime_path, sizeof(runtime_path), "%s/hermesrt.zip", runtime_root)
            >= (int)sizeof(runtime_path)) {
        fputs("hermes: runtime path is too long\n", stderr);
        return 70;
    }
    for (int i = 1; i + 1 < argc; ++i) {
        if (strcmp(argv[i], "--host") == 0) {
            setenv("HERMES_WEBUI_HOST", argv[i + 1], 1);
        } else if (strcmp(argv[i], "--port") == 0) {
            setenv("HERMES_WEBUI_PORT", argv[i + 1], 1);
        }
    }
    if (argc == 2 && (strcmp(argv[1], "--version") == 0 || strcmp(argv[1], "-V") == 0)) {
        static const char version[] = "Hermes Agent v0.21.2\n";
        (void)write(STDOUT_FILENO, version, sizeof(version) - 1);
        return 0;
    }
    char **python_argv = build_argv(argc, argv);
    if (python_argv == NULL) {
        fputs("hermes: unable to allocate argument vector\n", stderr);
        return 70;
    }

    PyImport_AppendInittab("binascii", PyInit_binascii);
    PyImport_AppendInittab("_struct", PyInit__struct);
    PyImport_AppendInittab("_socket", PyInit__socket);
    PyImport_AppendInittab("select", PyInit_select);
    PyImport_AppendInittab("math", PyInit_math);
    PyImport_AppendInittab("cmath", PyInit_cmath);
    PyImport_AppendInittab("_contextvars", PyInit__contextvars);
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.parse_argv = 0;

    PyStatus status = PyConfig_SetBytesArgv(&config, argc, python_argv);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    status = PyConfig_SetString(&config, &config.program_name, L"./hermes");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    wchar_t *runtime_zip = Py_DecodeLocale(runtime_path, NULL);
    if (runtime_zip == NULL) {
        fputs("hermes: unable to decode runtime path\n", stderr);
        PyConfig_Clear(&config);
        free(python_argv);
        return 70;
    }
    status = PyWideStringList_Append(&config.module_search_paths, runtime_zip);
    PyMem_RawFree(runtime_zip);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    const char *runtime_suffixes[] = {
        "/python", "/hermes", "/hermes-webui", "/python/site-packages"
    };
    for (size_t i = 0; i < sizeof(runtime_suffixes) / sizeof(runtime_suffixes[0]); ++i) {
        char path[PATH_MAX];
        if (snprintf(path, sizeof(path), "%s%s", runtime_path,
                     runtime_suffixes[i]) >= (int)sizeof(path)) {
            fputs("hermes: runtime path is too long\n", stderr);
            PyConfig_Clear(&config);
            free(python_argv);
            return 70;
        }
        wchar_t *wide_path = Py_DecodeLocale(path, NULL);
        if (wide_path == NULL) {
            fputs("hermes: unable to decode runtime path\n", stderr);
            PyConfig_Clear(&config);
            free(python_argv);
            return 70;
        }
        status = PyWideStringList_Append(&config.module_search_paths, wide_path);
        PyMem_RawFree(wide_path);
        if (PyStatus_Exception(status)) {
            PyConfig_Clear(&config);
            free(python_argv);
            Py_ExitStatusException(status);
        }
    }
    config.module_search_paths_set = 1;

    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }

    wchar_t **wide_argv = PyMem_RawCalloc((size_t)argc + 1, sizeof(*wide_argv));
    if (wide_argv == NULL) {
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return 70;
    }
    for (int i = 0; i < argc; ++i) {
        wide_argv[i] = Py_DecodeLocale(argv[i], NULL);
    }
    PySys_SetArgvEx(argc, wide_argv, 0);
    for (int i = 0; i < argc; ++i) PyMem_RawFree(wide_argv[i]);
    PyMem_RawFree(wide_argv);

    fflush(stderr);

    PyObject *bootstrap = PyImport_ImportModule("sitecustomize");
    if (bootstrap == NULL) {
        int result = report_python_error("import sitecustomize");
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return result;
    }
    Py_DECREF(bootstrap);

    if (configure_python_stdio() != 0) {
        int result = report_python_error("configure Python stdio");
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return result;
    }

    const char *entry_module = (argc > 1 && strcmp(argv[1], "webui") == 0)
        ? "server" : (argc > 1 && strcmp(argv[1], "upgrade") == 0)
            ? "hermes_cli.upgrade" : "hermes_cli.main";
    if (argc > 1 && (strcmp(argv[1], "webui") == 0 || strcmp(argv[1], "upgrade") == 0)) {
        /* The WebUI server owns its host/port overrides; remove the command
         * token so its normal argv handling sees the same arguments as when
         * launched directly. */
        PyRun_SimpleString(
            "import sys\n"
            "sys.argv = [sys.argv[0]] + sys.argv[2:]\n");
    }
    PyObject *module = PyImport_ImportModule(entry_module);
    if (module == NULL) {
        int system_exit = handle_system_exit();
        if (system_exit >= 0) {
            flush_python_stdio();
            Py_FinalizeEx();
            PyConfig_Clear(&config);
            free(python_argv);
            return system_exit;
        }
        int result = report_python_error(entry_module == NULL ? "import entry module" : "import entry module");
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return result;
    }
    PyObject *entrypoint = PyObject_GetAttrString(module, "main");
    Py_DECREF(module);
    if (entrypoint == NULL || !PyCallable_Check(entrypoint)) {
        Py_XDECREF(entrypoint);
        int result = report_python_error("find entrypoint main");
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return result;
    }
    PyObject *return_value = PyObject_CallNoArgs(entrypoint);
    Py_DECREF(entrypoint);
    if (return_value == NULL) {
        int system_exit = handle_system_exit();
        if (system_exit >= 0) {
            flush_python_stdio();
            Py_FinalizeEx();
            PyConfig_Clear(&config);
            free(python_argv);
            return system_exit;
        }
        int result = report_python_error("run entrypoint main");
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return result;
    }
    int result = 0;
    if (PyLong_Check(return_value)) {
        result = (int)PyLong_AsLong(return_value);
    }
    Py_DECREF(return_value);
    flush_python_stdio();
    Py_FinalizeEx();
    PyConfig_Clear(&config);
    free(python_argv);
    return result;
}
