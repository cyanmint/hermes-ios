"""Install framed stdout/stderr before application code imports run."""
import wasi_loader
wasi_loader.install_stdio()
