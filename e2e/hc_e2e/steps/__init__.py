# SPDX-License-Identifier: Apache-2.0
"""The Scenario's steps, one mixin class per phase family (hc_e2e.scenario composes them).

Each mixin's methods run on the Scenario: self.rec (Recorder), self.dev (Device), self.host
(BeanjarHost), self.a (the parsed options) and the state the earlier phases left on it.
"""
