import unittest

from devos.machine import BASE_ADDRESS, Machine
from devos.modes import MODES, MODE_BY_ID, source_lines
from devos.session import PALETTES, Session
from devos.simulation import Simulation


class SimulationTests(unittest.TestCase):
    def test_every_scene_has_resources_and_completes_builds(self):
        for mode in MODES:
            with self.subTest(mode=mode.id):
                self.assertIn(mode.palette, PALETTES)
                self.assertEqual(len(mode.phases), len(mode.phase_messages))
                for snippet in mode.snippets:
                    self.assertGreater(len(source_lines(snippet)), 20)
                sim = Simulation(mode, seed=42, wall_clock=0)
                visited = set()
                for _ in range(3000):
                    visited.add(sim.snippet.language)
                    sim.update(0.05)
                    self.assertGreaterEqual(sim.progress, 0)
                    self.assertLessEqual(sim.progress, 1)
                    self.assertLess(sim.phase, len(mode.phases))
                self.assertGreaterEqual(sim.builds, 3)
                self.assertGreaterEqual(sim.tests, 72)
                self.assertGreater(sim.total_lines, 100)
                self.assertTrue(all(0 <= v <= 1 for v in sim.history))
                if mode.hardware:
                    self.assertEqual(visited, {"C", "NASM"})

    def test_typewriter_is_a_prefix_of_source(self):
        sim = Simulation(MODES[0], seed=8)
        sim.update(0.3)
        self.assertGreater(sim.char_index, 0)
        self.assertEqual(sim.typed_lines[:-1], list(sim.lines[:sim.line_index]))
        self.assertTrue(sim.lines[sim.line_index].startswith(sim.typed_lines[-1]))

    def test_seed_reproduces_simulation(self):
        first = Simulation(MODES[0], seed=9, wall_clock=0)
        second = Simulation(MODES[0], seed=9, wall_clock=0)
        for _ in range(100):
            first.update(0.05)
            second.update(0.05)
        self.assertEqual(first.typed_lines, second.typed_lines)
        self.assertEqual(first.logs, second.logs)
        self.assertEqual(first.history, second.history)

    def test_machine_bytes_and_registers_change_without_corrupting_code(self):
        machine = Machine()
        initial = bytes(machine.memory)
        for _ in range(1000):
            machine.step()
        self.assertEqual(bytes(machine.memory[:224]), initial[:224])
        self.assertNotEqual(bytes(machine.memory[256:272]), initial[256:272])
        self.assertEqual(machine.registers["RSP"], 0xFFFF80000000FFF0)
        self.assertGreater(machine.registers["RCX"], 0)
        self.assertTrue(all(0 <= value < 2 ** 64 for value in machine.registers.values()))
        rows = list(machine.memory_rows(8, 8))
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(address >= BASE_ADDRESS and len(data) == 8 for address, data in rows))


class SessionTests(unittest.TestCase):
    def test_menu_wrap_cancel_and_confirm(self):
        session = Session()
        session.handle("tab")
        self.assertTrue(session.menu_open)
        session.handle("up")
        self.assertEqual(session.menu_index, len(MODES) - 1)
        session.handle("tab")
        self.assertEqual(session.menu_index, 0)
        session.handle("down")
        session.handle("escape")
        self.assertFalse(session.menu_open)
        self.assertTrue(session.running)
        self.assertEqual(session.mode_id, "ekza")
        session.handle("tab")
        session.handle("down")
        session.handle("accept")
        self.assertEqual(session.mode_id, "bare-metal")

    def test_switching_restores_progress_and_palette(self):
        session = Session(mode="bare-metal", seed=42)
        session.update(0.5)
        original = session.sim
        session.handle("theme")
        palette = session.theme
        session.activate("lab")
        session.update(2)
        session.activate("bare-metal")
        self.assertIs(session.sim, original)
        self.assertEqual(session.sim.elapsed, 0.5)
        self.assertEqual(session.theme, palette)
        self.assertNotEqual(session.sim.elapsed, session.simulations["lab"].elapsed)

    def test_pause_and_menu_do_not_advance_or_leak_keys(self):
        session = Session()
        session.handle("pause")
        session.update(1)
        self.assertEqual(session.sim.elapsed, 0)
        session.handle("pause")
        session.handle("tab")
        session.update(1)
        session.handle("next")
        self.assertEqual(session.sim.elapsed, 0)
        self.assertEqual(session.sim.file_index, 0)
        session.handle("escape")
        session.update(1)
        self.assertEqual(session.sim.elapsed, 1)

    def test_speed_is_bounded_and_quit_works_in_menu(self):
        session = Session()
        for _ in range(100):
            session.handle("faster")
        self.assertEqual(session.speed, 8)
        for _ in range(100):
            session.handle("slower")
        self.assertEqual(session.speed, 0.25)
        session.handle("tab")
        session.handle("quit")
        self.assertTrue(session.running)
        self.assertTrue(session.quit_confirm)
        session.handle("right")
        session.handle("accept")
        self.assertFalse(session.running)
