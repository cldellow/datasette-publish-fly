from sys import stdout
from datasette import hookimpl
from datasette.publish.common import (
    add_common_publish_arguments_and_options,
    fail_if_publish_binary_not_installed,
)
from datasette.utils import temporary_docker_directory
from subprocess import run, PIPE
import click
import json
import pathlib
import shutil

FLY_TOML = """
app = "{app}"
{mounts}
[[services]]
  internal_port = 8080
  protocol = "tcp"

  [services.concurrency]
    hard_limit = 25
    soft_limit = 20

  [[services.ports]]
    handlers = ["http"]
    port = "80"

  [[services.ports]]
    handlers = ["tls", "http"]
    port = "443"

  [[services.tcp_checks]]
    interval = 10000
    timeout = 2000
"""


@hookimpl
def publish_subcommand(publish):
    @publish.command()
    @add_common_publish_arguments_and_options
    @click.option("--spatialite", is_flag=True, help="Enable SpatialLite extension")
    @click.option(
        "--create-volume",
        type=click.IntRange(min=1),
        help="Create and attach volume of this size in GB",
    )
    @click.option("--volume", help="Name of existing volume to attach")
    @click.option(
        "--create-db",
        "-c",
        multiple=True,
        callback=validate_database_name,
        help="Names of read-write database files to create",
    )
    @click.option(
        "-a",
        "--app",
        help="Name of Fly app to deploy",
        required=True,
    )
    @click.option(
        "--generate-dir",
        type=click.Path(dir_okay=True, file_okay=False),
        help="Output generated application files and stop without deploying",
    )
    def fly(
        files,
        metadata,
        extra_options,
        branch,
        template_dir,
        plugins_dir,
        static,
        install,
        plugin_secret,
        version_note,
        secret,
        title,
        license,
        license_url,
        source,
        source_url,
        about,
        about_url,
        spatialite,
        create_volume,
        volume,
        create_db,
        app,
        generate_dir,
    ):
        if create_volume and volume:
            raise click.ClickException(
                "Use one of --volume or --create-volume but not both"
            )
        if create_db and not (volume or create_volume):
            raise click.ClickException(
                "--create-db must be used with --volume or --create-volume"
            )
        if (volume or create_volume) and not create_db:
            raise click.ClickException(
                "You must specify at least one --create-db name if using a volume"
            )
        fail_if_publish_binary_not_installed(
            "flyctl", "Fly", "https://fly.io/docs/getting-started/installing-flyctl/"
        )
        extra_metadata = {
            "title": title,
            "license": license,
            "license_url": license_url,
            "source": source,
            "source_url": source_url,
            "about": about,
            "about_url": about_url,
        }

        extra_options = extra_options or ""
        if create_db:
            for database_name in create_db:
                if not database_name.endswith(".db"):
                    database_name += ".db"
                extra_options += " /data/{}".format(database_name)
            extra_options += " --create"

        environment_variables = {}
        if plugin_secret:
            extra_metadata["plugins"] = {}
            for plugin_name, plugin_setting, setting_value in plugin_secret:
                environment_variable = (
                    "{}_{}".format(plugin_name, plugin_setting)
                    .upper()
                    .replace("-", "_")
                )
                environment_variables[environment_variable] = setting_value
                extra_metadata["plugins"].setdefault(plugin_name, {})[
                    plugin_setting
                ] = {"$env": environment_variable}
        with temporary_docker_directory(
            files,
            app,
            metadata,
            extra_options,
            branch,
            template_dir,
            plugins_dir,
            static,
            install,
            spatialite,
            version_note,
            secret,
            extra_metadata,
            environment_variables,
            port=8080,
        ):
            if not generate_dir:
                apps = existing_apps()
                if app not in apps:
                    # Attempt to create the app
                    result = run(
                        [
                            "flyctl",
                            "apps",
                            "create",
                            "--name",
                            app,
                            "--json",
                        ],
                        stderr=PIPE,
                        stdout=PIPE,
                    )
                    if result.returncode:
                        raise click.ClickException(
                            "Error calling 'flyctl apps create':\n\n{}".format(
                                # Don't include Usage: - could be confused for usage
                                # instructions for datasette publish fly
                                result.stderr.decode("utf-8")
                                .split("Usage:")[0]
                                .strip()
                            )
                        )

            if create_volume and not generate_dir:
                # TODO: Create a volume
                pass  # fly volumes create myapp_data --region lhr --size 40

            mounts = ""
            volume_name = "{}_volume".format(app)
            if create_volume:
                mounts = (
                    "\n[[mounts]]\n"
                    '  destination = "/data"\n'
                    '  source = "{}"\n'.format(volume_name)
                )

            fly_toml = FLY_TOML.format(app=app, mounts=mounts)

            if generate_dir:
                dir = pathlib.Path(generate_dir)
                if not dir.exists():
                    dir.mkdir()

                # Copy files from current directory to dir
                for file in pathlib.Path(".").glob("*"):
                    shutil.copy(str(file), str(dir / file.name))
                (dir / "fly.toml").write_text(fly_toml, "utf-8")
                return

            open("fly.toml", "w").write(fly_toml)
            # Now deploy it
            run(
                [
                    "flyctl",
                    "deploy",
                    ".",
                    "--app",
                    app,
                    "--config",
                    "fly.toml",
                    "--remote-only",
                ]
            )


def existing_apps():
    process = run(["flyctl", "apps", "list", "--json"], stdout=PIPE, stderr=PIPE)
    return [app["Name"] for app in json.loads(process.stdout)]


def validate_database_name(ctx, param, value):
    for name in value:
        if " " in name:
            raise click.BadParameter("Database name cannot contain spaces")
    return value
