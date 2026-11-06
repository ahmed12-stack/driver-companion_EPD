"""dc_template: copy this folder to start a new module.

    cp -r dc_template dc_speed       (then rename inside: dc_template -> dc_speed)

It shows the three things every module does: react to topics, publish to
topics, and run until stopped. As shipped it only logs GPS fixes; with --demo
it also says the speed out loud whenever it changes by 10 km/h, so you can
hear it working. It is not installed as a service.
"""
