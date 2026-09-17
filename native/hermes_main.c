#include <Python.h>
#include <stdlib.h>
#include <string.h>

static char **build_argv(int argc, char **argv) {
    char **result = calloc((size_t)argc + 3, sizeof(*result));
    if (result == NULL) {
        return NULL;
    }
    result[0] = argv[0];
    result[1] = "-m";
    result[2] = "hermes_cli.main";
    for (int i = 1; i < argc; ++i) {
        result[i + 2] = argv[i];
    }
    return result;
}

int main(int argc, char **argv) {
    char **python_argv = build_argv(argc, argv);
    if (python_argv == NULL) {
        fputs("hermes: unable to allocate argument vector\n", stderr);
        return 70;
    }

    /* The runtime archive is the only external payload.  CPython's normal
       importer handles pure-Python modules from this ZIP; native CPython
       modules are compiled into libpython during the iOS build. */
    if (setenv("PYTHONPATH", "./hermesrt.zip", 1) != 0) {
        fputs("hermes: unable to set PYTHONPATH\n", stderr);
        free(python_argv);
        return 70;
    }

    int result = Py_BytesMain(argc + 2, python_argv);
    free(python_argv);
    return result;
}
