<<<<<<< HEAD
# NIPXI — Battery Test System

Python-based control software for automated lithium-ion battery charge/discharge testing using a National Instruments PXI rack, a custom relay matrix (COM port), and the BLOSS Hub PCB.

This sub-repository is independent from the main BLOAST project repo.

---

## Purpose

Automate capacity and calendar aging tests on up to 8 Li-ion battery channels simultaneously:

- Charge each battery to a known SOC using CC-CV (SMU)
- Discharge each battery at a constant current (SMU)
- Measure voltage, current, and temperature per channel (DAQ + NTC)
- Log all data to SQLite and CSV for post-processing in the BLOAST ML pipeline

---

## Hardware

| Equipment | Model | Interface |
|---|---|---|
| PXI Chassis | NI PXI | NI-VISA |
| DAQ | NI 6363 (Slot 2) | nidaqmx |
| DMM | NI 4065 (Slot 3) | nidmm |
| SMU | NI 4140 / 4139 / 4130 (Slot 4-5) | nidcpower |
| Relay Matrix | NI 2569 (COM port controlled) | pyserial |
| Battery Hub PCB | BLOSS Hub Rev A | — |

The BLOSS Hub PCB connects up to 8 Li-ion batteries (3.5 V – 4.7 V, max 1 A per channel). Each channel has a 2 A fuse and an NTC thermistor for temperature monitoring.

---

## Project Structure

```
nipxi/
├── main.py                   Entry point
├── config/
│   ├── settings.py           Edit here: voltages, currents, ports, paths
│   └── devices.py            Edit here: channel/card assignments
├── hardware/
│   ├── base.py               Abstract hardware driver
│   ├── pxi_rack.py           PXI chassis (NI-VISA)
│   ├── smu.py                SMU charge/discharge (nidcpower)
│   ├── daq.py                DAQ acquisition (nidaqmx)
│   ├── relay_matrix.py       Relay matrix (pyserial)
│   └── temperature.py        NTC to degC conversion
├── test_control/
│   ├── charge_cycle.py       CC-CV charge sequence
│   ├── discharge_cycle.py    CC discharge sequence
│   ├── battery_test.py       Main orchestrator (all channels)
│   ├── safety_monitor.py     Limit checks + emergency stop
│   └── state_machine.py      Optional state tracker
├── data/
│   ├── logger.py             Logging setup
│   ├── storage.py            SQLite + CSV writer
│   └── report.py             Report generation (placeholder)
├── utils/
│   ├── constants.py          Project constants
│   ├── errors.py             Exception hierarchy
│   ├── helpers.py            Utilities
│   └── validators.py         Config/input validation
├── docs/
│   ├── architecture.md       System design overview
│   └── TODO.md               What still needs implementing
├── requirements.txt
└── .gitignore
```

---

## Quick Start (after implementation)

```bash
# 1. Clone or navigate to this repository
cd nipxi

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Edit configuration
#    - config/settings.py : voltages, COM port, PXI slot numbers
#    - config/devices.py  : channel mapping

# 5. Run
python main.py
python main.py --channels 1 2 3   # test only channels 1, 2, 3
python main.py --dry-run           # no hardware connection
```

---

## Configuring Devices

Before running, set these values in `config/settings.py`:

| Parameter | Default | Description |
|---|---|---|
| `RELAY_COM_PORT` | `"COM3"` | Serial port of relay matrix controller |
| `PXI_RESOURCE_DAQ` | `"PXI1Slot2"` | NI 6363 VISA resource |
| `PXI_RESOURCE_SMU1` | `"PXI1Slot4"` | NI SMU VISA resource |
| `CHARGE_CURRENT_A` | `0.5` | CC charge current |
| `DISCHARGE_CURRENT_A` | `0.5` | CC discharge current |
| `ACTIVE_CHANNELS` | `[1..8]` | Which channels to test |

---

## Remote Repository

> **TODO:** Set up remote Git repository and update this URL.
>
> ```bash
> git remote add origin <YOUR_REMOTE_URL_HERE>
> git push -u origin main
> ```

---

## Related Project

This software controls the hardware described in the main BLOAST repository:
- Battery type and specs: `hw/kicad/docs/COMPONENT_SPECIFICATIONS.md`
- PCB design: `hw/kicad/`
- Test protocol: `flowcharts/vi flowchart.md`
- Project roadmap: `roadmap.md`
=======
# BLOSSnipxie



## Getting started

To make it easy for you to get started with GitLab, here's a list of recommended next steps.

Already a pro? Just edit this README.md and make it your own. Want to make it easy? [Use the template at the bottom](#editing-this-readme)!

## Add your files

* [Create](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#create-a-file) or [upload](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#upload-a-file) files
* [Add files using the command line](https://docs.gitlab.com/topics/git/add_files/#add-files-to-a-git-repository) or push an existing Git repository with the following command:

```
cd existing_repo
git remote add origin https://gitlab.apps.ge-healthcare.net/550016588/blossnipxie.git
git branch -M main
git push -uf origin main
```

## Integrate with your tools

* [Set up project integrations](https://gitlab.apps.ge-healthcare.net/550016588/blossnipxie/-/settings/integrations)

## Collaborate with your team

* [Invite team members and collaborators](https://docs.gitlab.com/ee/user/project/members/)
* [Create a new merge request](https://docs.gitlab.com/ee/user/project/merge_requests/creating_merge_requests.html)
* [Automatically close issues from merge requests](https://docs.gitlab.com/ee/user/project/issues/managing_issues.html#closing-issues-automatically)
* [Enable merge request approvals](https://docs.gitlab.com/ee/user/project/merge_requests/approvals/)
* [Set auto-merge](https://docs.gitlab.com/user/project/merge_requests/auto_merge/)

## Test and Deploy

Use the built-in continuous integration in GitLab.

* [Get started with GitLab CI/CD](https://docs.gitlab.com/ee/ci/quick_start/)
* [Analyze your code for known vulnerabilities with Static Application Security Testing (SAST)](https://docs.gitlab.com/ee/user/application_security/sast/)
* [Deploy to Kubernetes, Amazon EC2, or Amazon ECS using Auto Deploy](https://docs.gitlab.com/ee/topics/autodevops/requirements.html)
* [Use pull-based deployments for improved Kubernetes management](https://docs.gitlab.com/ee/user/clusters/agent/)
* [Set up protected environments](https://docs.gitlab.com/ee/ci/environments/protected_environments.html)

***

# Editing this README

When you're ready to make this README your own, just edit this file and use the handy template below (or feel free to structure it however you want - this is just a starting point!). Thanks to [makeareadme.com](https://www.makeareadme.com/) for this template.

## Suggestions for a good README

Every project is different, so consider which of these sections apply to yours. The sections used in the template are suggestions for most open source projects. Also keep in mind that while a README can be too long and detailed, too long is better than too short. If you think your README is too long, consider utilizing another form of documentation rather than cutting out information.

## Name
Choose a self-explaining name for your project.

## Description
Let people know what your project can do specifically. Provide context and add a link to any reference visitors might be unfamiliar with. A list of Features or a Background subsection can also be added here. If there are alternatives to your project, this is a good place to list differentiating factors.

## Badges
On some READMEs, you may see small images that convey metadata, such as whether or not all the tests are passing for the project. You can use Shields to add some to your README. Many services also have instructions for adding a badge.

## Visuals
Depending on what you are making, it can be a good idea to include screenshots or even a video (you'll frequently see GIFs rather than actual videos). Tools like ttygif can help, but check out Asciinema for a more sophisticated method.

## Installation
Within a particular ecosystem, there may be a common way of installing things, such as using Yarn, NuGet, or Homebrew. However, consider the possibility that whoever is reading your README is a novice and would like more guidance. Listing specific steps helps remove ambiguity and gets people to using your project as quickly as possible. If it only runs in a specific context like a particular programming language version or operating system or has dependencies that have to be installed manually, also add a Requirements subsection.

## Usage
Use examples liberally, and show the expected output if you can. It's helpful to have inline the smallest example of usage that you can demonstrate, while providing links to more sophisticated examples if they are too long to reasonably include in the README.

## Support
Tell people where they can go to for help. It can be any combination of an issue tracker, a chat room, an email address, etc.

## Roadmap
If you have ideas for releases in the future, it is a good idea to list them in the README.

## Contributing
State if you are open to contributions and what your requirements are for accepting them.

For people who want to make changes to your project, it's helpful to have some documentation on how to get started. Perhaps there is a script that they should run or some environment variables that they need to set. Make these steps explicit. These instructions could also be useful to your future self.

You can also document commands to lint the code or run tests. These steps help to ensure high code quality and reduce the likelihood that the changes inadvertently break something. Having instructions for running tests is especially helpful if it requires external setup, such as starting a Selenium server for testing in a browser.

## Authors and acknowledgment
Show your appreciation to those who have contributed to the project.

## License
For open source projects, say how it is licensed.

## Project status
If you have run out of energy or time for your project, put a note at the top of the README saying that development has slowed down or stopped completely. Someone may choose to fork your project or volunteer to step in as a maintainer or owner, allowing your project to keep going. You can also make an explicit request for maintainers.
>>>>>>> 24197f8dcaeb01e92676face2f6e017df7510eed
