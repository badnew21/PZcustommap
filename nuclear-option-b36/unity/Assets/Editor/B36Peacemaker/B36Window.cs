using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace B36Peacemaker.Editor
{
    [Serializable]
    internal class B36Settings
    {
        public string baseAircraft = "";      // AircraftDefinition jsonKey; empty = find the Darkreach
        public string turretWeapon = "";      // WeaponMount jsonKey; empty = best native turret gun
        public List<string> extraBayWeapons = new List<string>();
        public bool shareBayDoors;            // all four bays open together on one set of native doors
        public string version = "0.1.0";
        public string outputFolder = "";

        const string PrefsKey = "B36Peacemaker.Settings";

        public static B36Settings Load()
        {
            var json = EditorPrefs.GetString(PrefsKey, "");
            var s = string.IsNullOrEmpty(json) ? null : JsonUtility.FromJson<B36Settings>(json);
            return s ?? new B36Settings();
        }

        public void Save()
        {
            EditorPrefs.SetString(PrefsKey, JsonUtility.ToJson(this));
        }
    }

    internal class B36Window : EditorWindow
    {
        B36Settings settings;
        string[] aircraftKeys = new string[0];
        string[] turretKeys = new string[0];
        string[] weaponKeys = new string[0];
        Vector2 scroll;

        [MenuItem("B-36/Builder")]
        static void Open()
        {
            GetWindow<B36Window>("B-36 Builder");
        }

        void OnEnable()
        {
            settings = B36Settings.Load();
            Refresh();
        }

        void Refresh()
        {
            if (!AssetDatabase.IsValidFolder(Blueprinter.BlueprinterSettings.GameAssetRootFolder)) return;
            var aircraft = new List<string> { "" };
            foreach (var d in B36Builder.Definitions()) aircraft.Add(B36Builder.JsonKeyOf(d));
            aircraftKeys = aircraft.ToArray();
            var turrets = new List<string> { "" };
            foreach (var c in B36Builder.TurretCandidates()) turrets.Add(c.jsonKey);
            turretKeys = turrets.ToArray();
            var index = Blueprinter.OpReferenceIndex.Load();
            weaponKeys = index != null ? index.Weapons.ToArray() : new string[0];
        }

        void OnGUI()
        {
            scroll = EditorGUILayout.BeginScrollView(scroll);
            EditorGUILayout.HelpBox("1. Run tools/convert_b36.py into " + B36Builder.ModRoot + "/Art.\n" +
                                    "2. Audit to see what the base aircraft offers, then Build, then Build .nobp.\n" +
                                    "Leave a choice empty for the automatic pick.", MessageType.Info);

            settings.baseAircraft = Popup("Base aircraft", settings.baseAircraft, aircraftKeys, "(Darkreach)");
            settings.turretWeapon = Popup("Turret weapon", settings.turretWeapon, turretKeys, "(best native turret gun)");
            settings.shareBayDoors = EditorGUILayout.Toggle(new GUIContent("Share bay doors", "Off: each bay gets its own copy of the native doors and opens on its own."), settings.shareBayDoors);

            EditorGUILayout.LabelField("Extra bomb bay weapons", EditorStyles.boldLabel);
            var remove = -1;
            for (var i = 0; i < settings.extraBayWeapons.Count; i++)
            {
                EditorGUILayout.BeginHorizontal();
                settings.extraBayWeapons[i] = Popup("", settings.extraBayWeapons[i], weaponKeys, "");
                if (GUILayout.Button("x", GUILayout.Width(22))) remove = i;
                EditorGUILayout.EndHorizontal();
            }
            if (remove >= 0) settings.extraBayWeapons.RemoveAt(remove);
            if (GUILayout.Button("Add bay weapon")) settings.extraBayWeapons.Add("");

            EditorGUILayout.Space();
            settings.version = EditorGUILayout.TextField("Mod version", settings.version);
            EditorGUILayout.BeginHorizontal();
            settings.outputFolder = EditorGUILayout.TextField("Output folder", settings.outputFolder);
            if (GUILayout.Button("...", GUILayout.Width(30)))
            {
                var picked = EditorUtility.OpenFolderPanel("Output folder for the .nobp", settings.outputFolder, "");
                if (!string.IsNullOrEmpty(picked)) settings.outputFolder = picked;
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.Space();
            settings.extraBayWeapons.RemoveAll(string.IsNullOrEmpty);
            settings.Save();
            if (GUILayout.Button("Refresh lists")) Refresh();
            if (GUILayout.Button("Audit base aircraft")) B36Builder.Audit(settings);
            if (GUILayout.Button("Build aircraft")) B36Builder.Build(settings);
            if (GUILayout.Button("Build .nobp")) B36Builder.Package(settings);
            EditorGUILayout.EndScrollView();
        }

        static string Popup(string label, string value, string[] keys, string emptyLabel)
        {
            var shown = new string[keys.Length];
            var index = 0;
            for (var i = 0; i < keys.Length; i++)
            {
                shown[i] = keys[i].Length == 0 ? emptyLabel : keys[i];
                if (keys[i] == value) index = i;
            }
            if (keys.Length == 0) return EditorGUILayout.TextField(label, value);
            var next = label.Length == 0 ? EditorGUILayout.Popup(index, shown) : EditorGUILayout.Popup(label, index, shown);
            return keys[next];
        }
    }

    // The editor's copy of b36_rig.json (plugin/RigData.cs has the runtime one).
    [Serializable]
    internal class B36Rig
    {
        public float length;
        public float span;
        public float height;
        public float[] cg;
        public B36RigPart[] parts;
        public B36RigTurret[] turrets;
        public B36RigLeg[] legs;
        public B36RigBay[] bays;
        public B36RigExhaust[] exhausts;
        public B36RigWingtips wingtips;

        public static Vector3 V(float[] a)
        {
            return new Vector3(a[0], a[1], a[2]);
        }
    }

    [Serializable]
    internal class B36RigPart
    {
        public string name;
        public string kind;
        public float[] pivot;
    }

    [Serializable]
    internal class B36RigTurret
    {
        public string name;
        public float[] pivot;
        public float retract;
        public float[] up;
        public float[] forward;
    }

    [Serializable]
    internal class B36RigLeg
    {
        public string leg;
        public float radius;
        public float[] center;
    }

    [Serializable]
    internal class B36RigBay
    {
        public int index;
        public float zFwd;
        public float zAft;
        public float hardpointY;
    }

    [Serializable]
    internal class B36RigExhaust
    {
        public string name;
        public bool jet;
        public float[] pos;
    }

    [Serializable]
    internal class B36RigWingtips
    {
        public float[] L;
        public float[] R;
    }

    [Serializable]
    internal class B36MaterialList
    {
        public B36MaterialSpec[] materials;
    }

    [Serializable]
    internal class B36MaterialSpec
    {
        public string name;
        public float[] color;
        public float metallic;
        public float smoothness;
        public bool transparent;
        public bool emissive;
        public string colorMap;
        public string normalMap;
        public string metallicMap;
        public string occlusionMap;
    }
}
