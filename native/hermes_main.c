#include <Python.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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
        Py_XDECREF(text);
        Py_XDECREF(type);
        Py_XDECREF(value);
        Py_XDECREF(traceback);
        PyErr_SetString(PyExc_RuntimeError, "Hermes embedded Python startup failed");
        PyErr_Print();
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

int main(int argc, char **argv) {
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
    status = PyWideStringList_Append(&config.module_search_paths,
                                     L"./hermesrt.zip");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
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

    PyObject *module = PyImport_ImportModule("hermes_cli.main");
    if (module == NULL) {
        int system_exit = handle_system_exit();
        if (system_exit >= 0) {
            flush_python_stdio();
            Py_FinalizeEx();
            PyConfig_Clear(&config);
            free(python_argv);
            return system_exit;
        }
        int result = report_python_error("import hermes_cli.main");
        Py_FinalizeEx();
        PyConfig_Clear(&config);
        free(python_argv);
        return result;
    }
    PyObject *entrypoint = PyObject_GetAttrString(module, "main");
    Py_DECREF(module);
    if (entrypoint == NULL || !PyCallable_Check(entrypoint)) {
        Py_XDECREF(entrypoint);
        int result = report_python_error("find hermes_cli.main.main");
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
        int result = report_python_error("run hermes_cli.main.main");
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
