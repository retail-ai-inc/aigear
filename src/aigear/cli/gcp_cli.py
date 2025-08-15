from aigear.infrastructure.gcp import Infra


def gcp_infra_init():
    gcp_infra = Infra()
    gcp_infra.create()
