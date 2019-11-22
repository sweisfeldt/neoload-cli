import sys
import traceback
import docker
import random
import time
import logging

def attachInfra(profile,rawspec):
    spec = parseInfraSpec(rawspec)
    if spec["provider"] == "docker":
        return attachLocalDockerInfra(profile,spec)
    else:
        raise NotImplementedError()

def detatchInfra(spec):
    logger = logging.getLogger("root")
    logger.info("detatchInfra: "+spec["provider"])
    if spec["provider"] == "docker":
        return detatchLocalDockerInfra(spec)
    else:
        logger.info("Attached infrastructure lacked a provider, so no detatched occured.")

_containerNamingPrefix = "neoload_cli"

def attachLocalDockerInfra(profile,spec):
    logger = logging.getLogger("root")

    client = docker.from_env()

    use_nts = profile.has_key("ntsurl") and profile.has_key("ntslogin")

    commonenv = {
        "NEOLOADWEB_URL": profile["url"],
        "NEOLOADWEB_TOKEN": profile["token"],
        "ZONE": profile["zone"],
    }
    ctrlenv = {
        "MODE": "Managed",
        "LEASE_SERVER": "NTS" if use_nts else "NLWEB",
    }
    ctrlenv.update(commonenv)
    if use_nts:
        nts = {
            "NTS_URL": profile["ntsurl"],
            "NTS_LOGIN": profile["ntslogin"],
        }
        ctrlenv.update(nts)
    else:
        logger.warning("Because there is no NTS configuration in this profile, license will be acquired from NLWEB.")

    container_ids = []
    network_id = None
    runId = str(random.randint(1,65535))
    randPortRange = str(random.randint(171,471)*100) # between 17100 and 47100
    randPortRange = 7100
    networkName = _containerNamingPrefix + runId + "_network"
    lgport = 7100
    try:
        network = client.networks.create(networkName, driver="bridge")
        logger.info("Created docker network '" + networkName + "'")
        spec["network_id"] = network.id

        lglinks = {}
        for x in range(spec["numOfLGs"]):
            lgport = randPortRange+x
            lgname = _containerNamingPrefix + runId + "_lg" + str(x+1)
            hostandport = {
                "LG_HOST": "host.docker.internal",#lgname,
                "LG_PORT": lgport
            }
            lgenv = commonenv.copy()
            lgenv.update(hostandport)
            lg = client.containers.run(
                image=spec["lgImage"],
                name = lgname,
                network = networkName,
                detach=True,
                auto_remove=True,
                environment=lgenv,
                ports={
                    "7100/tcp": lgport # backwards from usual docker-native spec
                }
            )
            lglinks[lg.id] = lgname
            container_ids.append(lg.id)
            logger.info("Attached load generator " + str(x+1) + ".")

        ctrl = client.containers.run(
            image=spec["ctrlImage"],
            name = _containerNamingPrefix + runId + "_ctrl",
            network = networkName,
            detach=True,
            auto_remove=True,
            environment=ctrlenv,
            links=lglinks
        )
        container_ids.append(ctrl.id)
        logger.info("Attached controller.")

        logger.info("Waiting for docker containers to be attached and ready.")
        time.sleep( 30 )

        spec["ready"] = True
    except Exception as err:
        logger.error("Unexpected error in 'attachLocalDockerInfra':", sys.exc_info()[0])
        traceback.print_exc()
        spec["ready"] = False

    spec["container_ids"] = container_ids

    return spec


def detatchLocalDockerInfra(spec):
    logger = logging.getLogger("root")
    client = docker.from_env()
    logger.debug(spec)
    for id in spec["container_ids"]:
        logger.info("Stopping container " + id)
        container = client.containers.get(id)
        container.stop()

    time.sleep( 5 )

    if spec["network_id"] is not None:
        network = client.networks.get(spec["network_id"])
        network.remove()

def parseInfraSpec(rawspec):
    #docker;2,neotys/neoload-loadgenerator:6.10
    ret = {
        "ready": False,
        "provider": None,
    }
    parts = rawspec.split("#")
    provider = parts[0].lower()

    ret["provider"] = provider

    if provider == "docker":
        numOfLGs = 1
        lgImage = "neotys/neoload-loadgenerator:latest"
        if len(parts)>1:
            lgparts = parts[1].split(",")
            if lgparts[0].isdigit():
                numOfLGs = int(lgparts[0])
            else:
                raise ValueError("Unknown docker spec for load generators.")

            if len(lgparts)>1:
                lgImage = lgparts[1]

        if numOfLGs > 10:
            raise ValueError("You cannot have more than 10 load generator containers on the same host.")

        ret["numOfLGs"] = numOfLGs
        ret["ctrlImage"] = lgImage.replace("-loadgenerator:","-controller:")
        ret["lgImage"] = lgImage
        return ret
    else:
        raise ValueError("Unknown provider prefix in infrastructure specification.")