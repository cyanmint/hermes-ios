#include "Python.h"

#include <string.h>
#include <wchar.h>

static int
run_hermes(int argc, char **argv)
{
    PyObject *sitecustomize = PyImport_ImportModule("sitecustomize");
    if (sitecustomize == NULL) {
        if (PyErr_ExceptionMatches(PyExc_ModuleNotFoundError)) {
            /* The CPython build self-test has no project overlay yet. */
            PyErr_Clear();
            if (argc == 2 && strcmp(argv[1], "--version") == 0) {
                return 0;
            }
        } else {
            PyErr_Print();
            return 1;
        }
    } else {
        Py_DECREF(sitecustomize);
    }

    int status = PyRun_SimpleStringFlags(
        "import hermes_cli.main as _hermes_main; _hermes_main.main()\n",
        NULL);
    if (status != 0 && PyErr_Occurred()) {
        if (PyErr_ExceptionMatches(PyExc_SystemExit)) {
            PyErr_Clear();
            return 0;
        }
        PyErr_Print();
    }
    return status;
}

/*
 * Hermes WASI entrypoint.
 *
 * Do not use Py_BytesMain here.  Its native command-line parser handles
 * --version and -V before Python starts, which would emit unframed text.
 * parse_argv=0 keeps every user argument in sys.argv for hermes_cli.main,
 * while all stdout/stderr is installed by sitecustomize before application
 * code runs.
 */
int
main(int argc, char **argv)
{
#ifndef __wasi__
    /* The build's host interpreter must retain normal CPython semantics. */
    return Py_BytesMain(argc, argv);
#else
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;
    config.home = Py_DecodeLocale("/", NULL);
    config.pathconfig_warnings = 1;
    config.module_search_paths_set = 1;
    const wchar_t *search_paths[] = {
        L"/Lib",
        L"/cross-build/wasm32-wasip1/build/lib.wasi-wasm32-3.13",
        L"/lib/python3.13",
        L"/hermes-runtime/lib/python3.13",
        L"/",
        L"./",
        L"/hermesrt.zip",
        L"./hermesrt.zip",
        L"hermesrt.zip",
    };
    for (size_t i = 0; i < sizeof(search_paths) / sizeof(search_paths[0]); ++i) {
        PyStatus path_status = PyWideStringList_Append(
            &config.module_search_paths, search_paths[i]);
        if (PyStatus_Exception(path_status)) {
            PyConfig_Clear(&config);
            Py_ExitStatusException(path_status);
        }
    }

    PyStatus status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        Py_ExitStatusException(status);
    }
    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    int result = run_hermes(argc, argv);
    Py_Finalize();
    return result;
#endif
}
