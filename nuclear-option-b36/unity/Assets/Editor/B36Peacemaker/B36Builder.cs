using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Blueprinter;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace B36Peacemaker.Editor
{
    // Builds the B-36 Peacemaker as a Blueprinter mod.
    //
    // The playable aircraft is a native one (the SFB-81 Darkreach by default): its flight
    // model, cockpit, weapons manager, bay doors and landing gear are kept and only re-laid
    // for the B-36. The B-36 exterior from tools/convert_b36.py replaces the native renderers.
    //   - Four hardpoint sets, one per B-36 bomb bay, each a copy of the native internal bay
    //     set (so they take the native bay weapons and open native bay doors before release).
    //   - One hardpoint set with eight stations at the B-36 turret positions, fitted with a
    //     native turret weapon (a mount whose prefab has a Turret), so the guns aim and fire
    //     with the game's own turret logic.
    //   - Native landing gear moved to the B-36 wheel positions and sizes.
    // Game types are reached by name through SerializedObject (like Blueprinter itself), so
    // nothing here depends on a particular game assembly. Every step that meets something
    // unexpected stops with an error that names it; the full record goes to
    // Logs/B36Peacemaker_build.txt.
    internal static class B36Builder
    {
        internal const string ModName = "B36Peacemaker";
        internal const string DisplayName = "B-36 Peacemaker";
        internal const string JsonKey = "B36Peacemaker";
        internal const string ModRoot = BlueprinterSettings.ModRootFolder + "/" + ModName;
        const string ArtRoot = ModRoot + "/Art";
        const string FbxPath = ArtRoot + "/B36.fbx";
        const string RigPath = ArtRoot + "/b36_rig.json";
        const string MaterialsPath = ArtRoot + "/b36_materials.json";
        const string MaterialRoot = ArtRoot + "/Materials";
        const string VisualPath = ModRoot + "/B36_Visual.prefab";
        const string PrefabPath = ModRoot + "/B36_Peacemaker.prefab";
        const string DefinitionPath = ModRoot + "/B36_Peacemaker.asset";
        const string ParametersPath = ModRoot + "/B36_Parameters.asset";
        const string HangarOpPath = ModRoot + "/Ops/OpAddB36ToHangars.asset";
        const string ReportPath = "Logs/B36Peacemaker_build.txt";

        // Must match plugin/B36Animator.cs Markers.
        const string RigFrameName = "B36_RigFrame";
        const string ModelName = "B36_Model";
        const string SourcePrefix = "B36_Src_";
        const string RestPrefix = "B36_Rest_";

        static readonly StringBuilder Report = new StringBuilder();

        // ------------------------------------------------------------ entry points

        internal static void Audit(B36Settings settings)
        {
            Report.Length = 0;
            try
            {
                var definition = FindBaseDefinition(settings);
                var prefab = PrefabOf(definition);
                Log("Base aircraft: " + JsonKeyOf(definition) + " (" + AssetDatabase.GetAssetPath(definition) + ")");
                Log("Prefab: " + AssetDatabase.GetAssetPath(prefab));
                Log("");
                Log("Hierarchy and components:");
                DumpHierarchy(prefab.transform, 1);
                foreach (var manager in ComponentsNamed(prefab, "WeaponManager"))
                    DumpWeaponManager(manager);
                Log("");
                Log("Turret weapon candidates (best first):");
                foreach (var c in TurretCandidates()) Log("  " + c.score + "  " + c.jsonKey + "  " + c.note);
                Log("");
                Log("Definition fields:");
                DumpProperties(new SerializedObject(definition), 1);
            }
            catch (Exception e)
            {
                Log("FAILED: " + e.Message);
                Debug.LogException(e);
            }
            WriteReport("audit");
        }

        internal static bool Build(B36Settings settings)
        {
            Report.Length = 0;
            try
            {
                if (!AssetDatabase.IsValidFolder(BlueprinterSettings.GameAssetRootFolder))
                    throw new InvalidOperationException("Run Blueprinter > Project Setup first: " + BlueprinterSettings.GameAssetRootFolder + " is missing.");
                var rig = LoadRig();
                ImportArt();
                var visual = BuildVisual(rig);
                var baseDefinition = FindBaseDefinition(settings);
                var definition = CreateDefinition(baseDefinition);
                BuildGameplayPrefab(settings, rig, baseDefinition, definition, visual);
                ConfigureDefinition(definition, AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath));
                RegisterAtHangars();
                ModInfo.Save(ModName, DisplayName, settings.version);
                AssetDatabase.SaveAssets();
                OpReferenceIndex.Refresh();
                Log("Built " + PrefabPath + " and " + DefinitionPath + ".");
                WriteReport("build");
                return true;
            }
            catch (Exception e)
            {
                Log("FAILED: " + e.Message);
                WriteReport("build");
                Debug.LogException(e);
                return false;
            }
        }

        internal static void Package(B36Settings settings)
        {
            if (!Build(settings)) return;
            if (string.IsNullOrEmpty(settings.outputFolder))
            {
                Debug.LogError("[B36] Choose an output folder outside Assets/Blueprinter first.");
                return;
            }
            Directory.CreateDirectory(settings.outputFolder);
            ModBuilder.Build(ModName, DisplayName, settings.version, settings.outputFolder);
            Debug.Log("[B36] Mod built into " + settings.outputFolder + ". Copy the .nobp and the B36Peacemaker.dll plugin into BepInEx/plugins.");
        }

        // ------------------------------------------------------------ art

        static B36Rig LoadRig()
        {
            var text = AssetDatabase.LoadAssetAtPath<TextAsset>(RigPath);
            if (text == null || AssetDatabase.LoadAssetAtPath<GameObject>(FbxPath) == null)
                throw new InvalidOperationException("Missing " + FbxPath + " or " + RigPath + ". Run tools/convert_b36.py with --out " + ArtRoot + ".");
            var rig = JsonUtility.FromJson<B36Rig>(text.text);
            if (rig == null || rig.parts == null || rig.turrets == null || rig.bays == null || rig.bays.Length != 4)
                throw new InvalidOperationException("b36_rig.json is not the expected rig.");
            return rig;
        }

        static void ImportArt()
        {
            var specs = JsonUtility.FromJson<B36MaterialList>(AssetDatabase.LoadAssetAtPath<TextAsset>(MaterialsPath).text);
            foreach (var guid in AssetDatabase.FindAssets("t:Texture2D", new[] { ArtRoot + "/Textures" }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                var importer = (TextureImporter)AssetImporter.GetAtPath(path);
                var file = Path.GetFileNameWithoutExtension(path);
                var normal = file.EndsWith("_normal");
                var linear = normal || file.EndsWith("_metallic") || file.EndsWith("_occlusion");
                var type = normal ? TextureImporterType.NormalMap : TextureImporterType.Default;
                if (importer.textureType == type && importer.sRGBTexture == !linear) continue;
                importer.textureType = type;
                importer.sRGBTexture = !linear;
                importer.mipmapEnabled = true;
                importer.SaveAndReimport();
            }

            BlueprinterAssets.EnsureFolder(MaterialRoot);
            var shader = Shader.Find("Standard");
            if (shader == null) throw new InvalidOperationException("The Standard shader is not available.");
            var model = (ModelImporter)AssetImporter.GetAtPath(FbxPath);
            foreach (var spec in specs.materials)
            {
                var path = MaterialRoot + "/" + spec.name + ".mat";
                var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (mat == null)
                {
                    mat = new Material(shader);
                    AssetDatabase.CreateAsset(mat, path);
                }
                mat.shader = shader;
                ConfigureMaterial(mat, spec);
                EditorUtility.SetDirty(mat);
                model.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), spec.name), mat);
            }

            model.globalScale = 1f;
            model.useFileScale = true;
            model.importAnimation = false;
            model.animationType = ModelImporterAnimationType.None;
            model.importBlendShapes = false;
            model.importCameras = false;
            model.importLights = false;
            model.isReadable = false;
            model.meshCompression = ModelImporterMeshCompression.Off;
            model.importNormals = ModelImporterNormals.Import;
            model.importTangents = ModelImporterTangents.CalculateMikk;
            model.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
            model.SaveAndReimport();
            Log("Imported " + FbxPath + " with " + specs.materials.Length + " materials.");
        }

        static void ConfigureMaterial(Material mat, B36MaterialSpec spec)
        {
            var color = new Color(spec.color[0], spec.color[1], spec.color[2], spec.color[3]);
            mat.SetColor("_Color", color);
            mat.SetFloat("_Metallic", spec.metallic);
            mat.SetFloat("_Glossiness", spec.smoothness);
            mat.SetFloat("_GlossMapScale", 1f);
            mat.SetTexture("_MainTex", Texture(spec.colorMap));
            mat.SetTexture("_BumpMap", Texture(spec.normalMap));
            mat.SetTexture("_MetallicGlossMap", Texture(spec.metallicMap));
            mat.SetTexture("_OcclusionMap", Texture(spec.occlusionMap));
            SetKeyword(mat, "_NORMALMAP", !string.IsNullOrEmpty(spec.normalMap));
            SetKeyword(mat, "_METALLICGLOSSMAP", !string.IsNullOrEmpty(spec.metallicMap));
            if (spec.transparent)
            {
                mat.SetFloat("_Mode", 3f);
                mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.One);
                mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                mat.SetInt("_ZWrite", 0);
                SetKeyword(mat, "_ALPHAPREMULTIPLY_ON", true);
                mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
            }
            if (spec.emissive)
            {
                mat.SetColor("_EmissionColor", new Color(color.r, color.g, color.b) * 2f);
                SetKeyword(mat, "_EMISSION", true);
                mat.globalIlluminationFlags = MaterialGlobalIlluminationFlags.None;
            }
        }

        static Texture2D Texture(string file)
        {
            if (string.IsNullOrEmpty(file)) return null;
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(ArtRoot + "/Textures/" + file);
            if (tex == null) throw new InvalidOperationException("Missing texture " + file);
            return tex;
        }

        static void SetKeyword(Material mat, string keyword, bool on)
        {
            if (on) mat.EnableKeyword(keyword);
            else mat.DisableKeyword(keyword);
        }

        // The visual prefab: a rig frame whose local space is B-36 model space, holding the
        // imported model placed so that every rig point lands where the rig says. The fit is
        // solved from named landmarks, so it does not depend on how Unity converted the FBX
        // axes, and then checked against every moving part.
        static GameObject BuildVisual(B36Rig rig)
        {
            var fbx = AssetDatabase.LoadAssetAtPath<GameObject>(FbxPath);
            var frame = new GameObject(RigFrameName);
            try
            {
                var model = (GameObject)PrefabUtility.InstantiatePrefab(fbx);
                PrefabUtility.UnpackPrefabInstance(model, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
                model.name = ModelName;
                model.transform.SetParent(frame.transform, false);
                var names = new[] { "Turret_Nose", "Turret_Tail", "Prop_L1", "Prop_R1", "Rudder" };
                var imported = new Vector3[names.Length];
                var wanted = new Vector3[names.Length];
                for (var i = 0; i < names.Length; i++)
                {
                    imported[i] = model.transform.InverseTransformPoint(FindNamed(model.transform, names[i]).position);
                    wanted[i] = Landmark(rig, names[i]);
                }
                FitModel(model.transform, imported, wanted);

                var worst = 0f;
                var worstName = "";
                foreach (var p in rig.parts) Check(frame.transform, model.transform, p.name, B36Rig.V(p.pivot), ref worst, ref worstName);
                foreach (var t in rig.turrets) Check(frame.transform, model.transform, t.name, B36Rig.V(t.pivot), ref worst, ref worstName);
                if (worst > 0.05f)
                    throw new InvalidOperationException("Model and rig disagree by " + worst.ToString("0.000") + " m at " + worstName + ". Re-run tools/convert_b36.py.");
                Log("Model fitted to the rig; largest pivot error " + (worst * 1000f).ToString("0.0") + " mm (" + worstName + ").");

                foreach (var r in frame.GetComponentsInChildren<Renderer>(true))
                {
                    r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
                    r.receiveShadows = true;
                }
                var saved = PrefabUtility.SaveAsPrefabAsset(frame, VisualPath);
                return saved;
            }
            finally
            {
                Object.DestroyImmediate(frame);
            }
        }

        static Vector3 Landmark(B36Rig rig, string name)
        {
            foreach (var p in rig.parts) if (p.name == name) return B36Rig.V(p.pivot);
            foreach (var t in rig.turrets) if (t.name == name) return B36Rig.V(t.pivot);
            throw new InvalidOperationException("Rig has no landmark " + name);
        }

        static void Check(Transform frame, Transform model, string name, Vector3 want, ref float worst, ref string worstName)
        {
            var got = frame.InverseTransformPoint(FindNamed(model, name).position);
            var err = Vector3.Distance(got, want);
            if (err > worst) { worst = err; worstName = name; }
        }

        // Solves the similarity (rotation, uniform scale, optional mirror, offset) taking the
        // imported landmark positions to the rig's, and applies it to the model transform.
        static void FitModel(Transform model, Vector3[] a, Vector3[] b)
        {
            var da = Basis(a[0] - a[1], a[3] - a[2], a[4] - a[1]);
            var db = Basis(b[0] - b[1], b[3] - b[2], b[4] - b[1]);
            var m = db * da.inverse;  // linear part, imported -> rig
            var cx = m.GetColumn(0);
            var cy = m.GetColumn(1);
            var cz = m.GetColumn(2);
            var scale = (((Vector3)cx).magnitude + ((Vector3)cy).magnitude + ((Vector3)cz).magnitude) / 3f;
            if (Mathf.Abs(Vector3.Dot(cx, cy)) > 0.01f * scale * scale || Mathf.Abs(((Vector3)cx).magnitude - scale) > 0.01f * scale)
                throw new InvalidOperationException("The imported model is sheared or unevenly scaled; check the FBX import settings.");
            var det = Vector3.Dot(Vector3.Cross(cx, cy), cz);
            var mirror = det < 0f;
            // With a mirror, fold it into a negative x scale so the rotation is proper.
            var rx = (Vector3)cx / scale * (mirror ? -1f : 1f);
            var ry = (Vector3)cy / scale;
            var rz = (Vector3)cz / scale;
            var rot = Quaternion.LookRotation(rz, ry);
            model.localRotation = rot;
            model.localScale = new Vector3(mirror ? -scale : scale, scale, scale);
            var p0 = rot * Vector3.Scale(model.localScale, a[1]);
            model.localPosition = b[1] - p0;
            if (Vector3.Distance(rot * Vector3.right, rx) > 0.01f)
                throw new InvalidOperationException("Could not fit the model orientation.");
            Log("Model fit: scale " + scale.ToString("0.0000") + (mirror ? ", mirrored" : "") + ", rotation " + rot.eulerAngles);
        }

        static Matrix4x4 Basis(Vector3 x, Vector3 y, Vector3 z)
        {
            var m = Matrix4x4.identity;
            m.SetColumn(0, x);
            m.SetColumn(1, y);
            m.SetColumn(2, z);
            return m;
        }

        // ------------------------------------------------------------ base aircraft

        static Object FindBaseDefinition(B36Settings settings)
        {
            Object fallback = null;
            foreach (var definition in Definitions())
            {
                var key = JsonKeyOf(definition);
                if (!string.IsNullOrEmpty(settings.baseAircraft))
                {
                    if (key == settings.baseAircraft) return definition;
                    continue;
                }
                var text = (key + " " + definition.name + " " + StringField(definition, "unitName") + " " + StringField(definition, "code")).ToLowerInvariant();
                if (text.Contains("darkreach") || text.Contains("sfb-81")) return definition;
                if (fallback == null && text.Contains("bomber")) fallback = definition;
            }
            if (fallback != null) return fallback;
            throw new InvalidOperationException("Base aircraft '" + (settings.baseAircraft ?? "Darkreach") + "' not found. Pick one in the B-36 window (it lists every AircraftDefinition).");
        }

        internal static List<Object> Definitions()
        {
            var list = new List<Object>();
            foreach (var guid in AssetDatabase.FindAssets("t:AircraftDefinition", new[] { BlueprinterSettings.GameAssetRootFolder }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (BlueprinterAssets.IsStaleGameAssetPath(path)) continue;
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset != null && !string.IsNullOrEmpty(JsonKeyOf(asset))) list.Add(asset);
            }
            return list;
        }

        static GameObject PrefabOf(Object definition)
        {
            var prefab = new SerializedObject(definition).FindProperty("unitPrefab");
            var go = prefab != null ? prefab.objectReferenceValue as GameObject : null;
            if (go == null) throw new InvalidOperationException(JsonKeyOf(definition) + " has no unitPrefab.");
            return go;
        }

        static Object CreateDefinition(Object baseDefinition)
        {
            var definition = AssetDatabase.LoadMainAssetAtPath(DefinitionPath);
            if (definition == null || definition.GetType() != baseDefinition.GetType())
            {
                if (definition != null) AssetDatabase.DeleteAsset(DefinitionPath);
                definition = Object.Instantiate(baseDefinition);
                definition.name = "B36_Peacemaker";
                AssetDatabase.CreateAsset(definition, DefinitionPath);
            }
            return definition;
        }

        static void ConfigureDefinition(Object definition, GameObject prefab)
        {
            var so = new SerializedObject(definition);
            SetString(so, "jsonKey", JsonKey);
            SetString(so, "unitName", "B-36J Peacemaker");
            SetString(so, "code", "B-36J");
            SetString(so, "description", "Convair B-36J Peacemaker. Six piston engines and four jets, four bomb bays and eight remote turrets with twin 20 mm guns. Flight model adapted from the SFB-81.");
            SetFloat(so, "length", 49.40f);
            SetFloat(so, "width", 70.10f);
            SetFloat(so, "height", 14.25f);
            SetObject(so, "unitPrefab", prefab);

            var parametersProperty = so.FindProperty("aircraftParameters");
            if (parametersProperty != null && parametersProperty.objectReferenceValue != null)
            {
                var parameters = AssetDatabase.LoadMainAssetAtPath(ParametersPath);
                if (parameters == null || AssetDatabase.GetAssetPath(parametersProperty.objectReferenceValue) != ParametersPath)
                {
                    if (parameters == null)
                    {
                        parameters = Object.Instantiate(parametersProperty.objectReferenceValue);
                        parameters.name = "B36_Parameters";
                        AssetDatabase.CreateAsset(parameters, ParametersPath);
                    }
                    parametersProperty.objectReferenceValue = parameters;
                }
                var p = new SerializedObject(parameters);
                SetString(p, "aircraftName", "B-36J Peacemaker");
                p.ApplyModifiedPropertiesWithoutUndo();
            }
            so.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(definition);
        }

        static void RegisterAtHangars()
        {
            BlueprinterAssets.EnsureFolder(ModRoot + "/Ops");
            var op = AssetDatabase.LoadAssetAtPath<OpAddAircraftToHangars>(HangarOpPath);
            if (op == null)
            {
                op = ScriptableObject.CreateInstance<OpAddAircraftToHangars>();
                AssetDatabase.CreateAsset(op, HangarOpPath);
            }
            op.aircraftJsonKey = JsonKey;
            op.hangars = new List<OpAddAircraftToHangars.HangarTarget>();
            OpReferenceIndex.Refresh();
            var index = OpReferenceIndex.Load();
            if (index != null)
                foreach (var hangar in index.HangarUnits)
                    op.hangars.Add(new OpAddAircraftToHangars.HangarTarget { hangarUnitJsonKey = hangar.jsonKey, hangarNames = new List<string>(hangar.hangarNames) });
            EditorUtility.SetDirty(op);
            Log("Hangar op lists " + op.hangars.Count + " hangar units; edit " + HangarOpPath + " to limit where the B-36 spawns.");
        }

        // ------------------------------------------------------------ gameplay prefab

        static void BuildGameplayPrefab(B36Settings settings, B36Rig rig, Object baseDefinition, Object definition, GameObject visual)
        {
            var root = Object.Instantiate(PrefabOf(baseDefinition));
            try
            {
                root.name = "B36_Peacemaker";
                var bounds = NativeBounds(root);
                Log("Native exterior bounds: " + bounds.size + " centred " + bounds.center + ". B-36: " +
                    new Vector3(rig.span, rig.height, rig.length));

                foreach (var r in root.GetComponentsInChildren<Renderer>(true)) r.enabled = false;
                foreach (var lod in root.GetComponentsInChildren<LODGroup>(true)) lod.enabled = false;

                // The B-36 centre of gravity goes on the native root, which is the native one's.
                var frame = ((GameObject)PrefabUtility.InstantiatePrefab(visual)).transform;
                PrefabUtility.UnpackPrefabInstance(frame.gameObject, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
                frame.name = RigFrameName;
                frame.SetParent(root.transform, false);
                frame.localPosition = -B36Rig.V(rig.cg);
                frame.localRotation = Quaternion.identity;
                frame.localScale = Vector3.one;

                var manager = Single(root, "WeaponManager");
                var turretMount = PickTurretMount(settings);
                LayOutWeapons(settings, rig, root.transform, frame, manager, turretMount);
                LayOutGear(rig, root.transform, frame);
                MoveEffects(rig, root.transform, frame);
                ConfigureAircraft(root, frame, definition);

                foreach (var t in root.GetComponentsInChildren<Transform>(true))
                    GameObjectUtility.RemoveMonoBehavioursWithMissingScript(t.gameObject);
                PrefabUtility.SaveAsPrefabAsset(root, PrefabPath);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        static Bounds NativeBounds(GameObject root)
        {
            var b = new Bounds(Vector3.zero, Vector3.zero);
            var first = true;
            foreach (var r in root.GetComponentsInChildren<MeshRenderer>(true))
            {
                if (r.gameObject.layer == 3) continue;  // cockpit layer
                if (first) { b = r.bounds; first = false; }
                else b.Encapsulate(r.bounds);
            }
            return b;
        }

        static void ConfigureAircraft(GameObject root, Transform frame, Object definition)
        {
            var aircraft = Single(root, "Aircraft");
            var so = new SerializedObject(aircraft);
            var defProperty = so.FindProperty("definition");
            if (defProperty == null) throw new InvalidOperationException("Aircraft has no 'definition' field.");
            defProperty.objectReferenceValue = definition;
            var exterior = so.FindProperty("exteriorRenderers");
            if (exterior == null) throw new InvalidOperationException("Aircraft has no 'exteriorRenderers' field.");
            var renderers = frame.GetComponentsInChildren<Renderer>(true);
            exterior.arraySize = renderers.Length;
            for (var i = 0; i < renderers.Length; i++)
            {
                exterior.GetArrayElementAtIndex(i).objectReferenceValue = renderers[i];
                renderers[i].enabled = true;
            }
            so.ApplyModifiedPropertiesWithoutUndo();
            Log("Aircraft: definition set, " + renderers.Length + " B-36 exterior renderers.");
        }

        // ------------------------------------------------------------ weapons

        static void LayOutWeapons(B36Settings settings, B36Rig rig, Transform root, Transform frame, Component manager, Object turretMount)
        {
            var so = new SerializedObject(manager);
            var sets = so.FindProperty("hardpointSets");
            if (sets == null || !sets.isArray || sets.arraySize == 0)
                throw new InvalidOperationException("WeaponManager has no hardpointSets.");

            var template = -1;
            for (var i = 0; i < sets.arraySize && template < 0; i++)
            {
                var points = sets.GetArrayElementAtIndex(i).FindPropertyRelative("hardpoints");
                for (var j = 0; points != null && j < points.arraySize; j++)
                {
                    var doors = points.GetArrayElementAtIndex(j).FindPropertyRelative("bayDoors");
                    if (doors != null && doors.arraySize > 0) { template = i; break; }
                }
            }
            if (template < 0)
            {
                template = 0;
                Log("WARNING: the base aircraft has no internal bay set; bays copy set 0 and have no doors to open.");
            }
            var templateName = sets.GetArrayElementAtIndex(template).FindPropertyRelative("name").stringValue;
            Log("Bay template: set " + template + " '" + templateName + "' of " + sets.arraySize + ".");

            // Five copies of the template, nothing else: four bays and the turret set.
            for (var k = 0; k < 4; k++) sets.InsertArrayElementAtIndex(template);
            for (var i = sets.arraySize - 1; i >= 0; i--)
                if (i < template || i > template + 4) sets.DeleteArrayElementAtIndex(i);
            so.ApplyModifiedPropertiesWithoutUndo();
            so.Update();
            sets = so.FindProperty("hardpointSets");

            var extraBay = new List<Object>();
            foreach (var key in settings.extraBayWeapons)
            {
                var mount = FindWeaponMount(key);
                if (mount != null) extraBay.Add(mount);
                else Log("WARNING: bay weapon '" + key + "' not found.");
            }

            var sharedDoorClones = new Dictionary<Object, Object>();
            for (var k = 0; k < 4; k++)
            {
                var bay = rig.bays[k];
                var set = sets.GetArrayElementAtIndex(k);
                set.FindPropertyRelative("name").stringValue = "Bomb Bay " + bay.index;
                ClearArray(set, "precludingHardpointSets");
                var points = set.FindPropertyRelative("hardpoints");
                var count = points.arraySize;
                var doorClones = settings.shareBayDoors ? sharedDoorClones : new Dictionary<Object, Object>();
                for (var j = 0; j < count; j++)
                {
                    var hp = points.GetArrayElementAtIndex(j);
                    var field = TransformField(hp);
                    var original = (Transform)field.objectReferenceValue;
                    var station = Object.Instantiate(original.gameObject, original.parent).transform;
                    station.name = "B36_Bay" + bay.index + "_HP" + j;
                    station.position = frame.TransformPoint(BayStation(bay, j, count));
                    station.rotation = root.rotation;
                    field.objectReferenceValue = station;

                    var doors = hp.FindPropertyRelative("bayDoors");
                    for (var d = 0; doors != null && d < doors.arraySize; d++)
                    {
                        var element = doors.GetArrayElementAtIndex(d);
                        var door = element.objectReferenceValue;
                        if (door == null) continue;
                        Object clone;
                        if (!doorClones.TryGetValue(door, out clone))
                        {
                            clone = CloneDoor(door, frame, bay.index, doorClones.Count);
                            doorClones.Add(door, clone);
                        }
                        element.objectReferenceValue = clone;
                    }
                }
                foreach (var mount in extraBay) AddOption(set, mount);
                Log("Bomb Bay " + bay.index + ": " + count + " stations, " + Options(set) + ".");
            }

            // Turret set: one station per B-36 turret, fitted with the native turret weapon.
            var turrets = sets.GetArrayElementAtIndex(4);
            turrets.FindPropertyRelative("name").stringValue = "Defensive Turrets";
            ClearArray(turrets, "precludingHardpointSets");
            var options = turrets.FindPropertyRelative("weaponOptions");
            options.arraySize = 0;
            if (turretMount != null) AddOption(turrets, turretMount);
            var tp = turrets.FindPropertyRelative("hardpoints");
            tp.arraySize = rig.turrets.Length;
            var outward = TurretBodySign(turretMount);
            for (var j = 0; j < rig.turrets.Length; j++)
            {
                var t = rig.turrets[j];
                var hp = tp.GetArrayElementAtIndex(j);
                var field = TransformField(hp);
                var original = (Transform)field.objectReferenceValue;
                var station = Object.Instantiate(original.gameObject, original.parent).transform;
                station.name = "B36_" + t.name;
                var children = new List<GameObject>();
                foreach (Transform child in station) children.Add(child.gameObject);
                foreach (var child in children) Object.DestroyImmediate(child);
                // Gun at the extended turret ring; the mount's body faces out of the fuselage.
                var up = B36Rig.V(t.up);
                var fwd = Vector3.ProjectOnPlane(B36Rig.V(t.forward), up).normalized;
                var mountUp = up * outward;
                station.position = frame.TransformPoint(B36Rig.V(t.pivot) + up * t.retract);
                station.rotation = frame.rotation * Quaternion.LookRotation(fwd, mountUp);
                field.objectReferenceValue = station;
                var doors = hp.FindPropertyRelative("bayDoors");
                if (doors != null) doors.arraySize = 0;
                var duration = hp.FindPropertyRelative("doorOpenDuration");
                if (duration != null) duration.floatValue = 0f;
                var marker = new GameObject(SourcePrefix + t.name).transform;
                marker.SetParent(station, false);
            }
            so.ApplyModifiedPropertiesWithoutUndo();
            Log("Defensive Turrets: " + rig.turrets.Length + " stations, " + (turretMount != null ? JsonKeyOf(turretMount) : "NO TURRET WEAPON FOUND") +
                (outward < 0 ? " (mount hangs below its station, so stations are flipped to face outward)" : "") + ".");
            FixLoadouts();
        }

        // Stations along the bay roof: side by side for two, in a row along the bay otherwise.
        static Vector3 BayStation(B36RigBay bay, int j, int count)
        {
            var z = (bay.zFwd + bay.zAft) / 2f;
            var x = 0f;
            if (count == 2) x = j == 0 ? -0.45f : 0.45f;
            else if (count > 2) z = Mathf.Lerp(bay.zFwd - 0.6f, bay.zAft + 0.6f, j / (float)(count - 1));
            return new Vector3(x, bay.hardpointY, z);
        }

        // Each bay gets its own copy of the native doors, so each opens on its own. The rest
        // marker records the copy's prefab pose for the plugin.
        static Object CloneDoor(Object door, Transform frame, int bay, int n)
        {
            var go = door as GameObject ?? (door as Component).gameObject;
            var copy = Object.Instantiate(go, go.transform.parent);
            copy.name = go.name + "_B36Bay" + bay;
            var key = "Door_" + bay + "_" + n;
            new GameObject(SourcePrefix + key).transform.SetParent(copy.transform, false);
            var rest = new GameObject(RestPrefix + key).transform;
            rest.SetParent(frame, false);
            rest.SetPositionAndRotation(copy.transform.position, copy.transform.rotation);
            if (door is GameObject) return copy;
            var type = door.GetType();
            var all = go.GetComponents(type);
            var index = Array.IndexOf(all, door);
            return copy.GetComponents(type)[index];
        }

        static SerializedProperty TransformField(SerializedProperty hardpoint)
        {
            SerializedProperty found = null;
            foreach (var child in Children(hardpoint))
            {
                if (child.propertyType != SerializedPropertyType.ObjectReference) continue;
                if (!(child.objectReferenceValue is Transform)) continue;
                if (found == null || child.name == "transform") found = child;
            }
            if (found == null)
                throw new InvalidOperationException("Hardpoint " + hardpoint.propertyPath + " has no Transform field; see the audit.");
            return found;
        }

        static IEnumerable<SerializedProperty> Children(SerializedProperty parent)
        {
            var it = parent.Copy();
            var end = parent.GetEndProperty();
            var depth = parent.depth + 1;
            // Next rather than NextVisible: game fields may be hidden in the inspector.
            if (!it.Next(true)) yield break;
            while (!SerializedProperty.EqualContents(it, end))
            {
                if (it.depth == depth) yield return it.Copy();
                if (!it.Next(it.depth < depth)) yield break;
            }
        }

        static void AddOption(SerializedProperty set, Object mount)
        {
            var options = set.FindPropertyRelative("weaponOptions");
            for (var i = 0; i < options.arraySize; i++)
                if (options.GetArrayElementAtIndex(i).objectReferenceValue == mount) return;
            options.arraySize++;
            options.GetArrayElementAtIndex(options.arraySize - 1).objectReferenceValue = mount;
        }

        static string Options(SerializedProperty set)
        {
            var options = set.FindPropertyRelative("weaponOptions");
            var names = new List<string>();
            for (var i = 0; i < options.arraySize; i++)
            {
                var o = options.GetArrayElementAtIndex(i).objectReferenceValue;
                names.Add(o != null ? JsonKeyOf(o) : "(empty)");
            }
            return names.Count + " weapons: " + string.Join(", ", names.ToArray());
        }

        static void ClearArray(SerializedProperty parent, string name)
        {
            var p = parent.FindPropertyRelative(name);
            if (p != null && p.isArray) p.arraySize = 0;
        }

        // Saved loadouts on the copied definition count stations by set; keep them in step.
        static void FixLoadouts()
        {
            var definition = AssetDatabase.LoadMainAssetAtPath(DefinitionPath);
            var so = new SerializedObject(definition);
            var it = so.GetIterator();
            var fixedCount = 0;
            while (it.Next(true))
            {
                if (!it.isArray || it.name != "weapons" || it.propertyType == SerializedPropertyType.String) continue;
                if (!it.propertyPath.ToLowerInvariant().Contains("loadout")) continue;
                var weapons = it.Copy();
                weapons.arraySize = 5;
                for (var i = 0; i < 5; i++)
                {
                    var e = weapons.GetArrayElementAtIndex(i);
                    if (i == 4 && e.propertyType == SerializedPropertyType.ObjectReference) e.objectReferenceValue = null;
                }
                fixedCount++;
            }
            if (fixedCount > 0)
            {
                so.ApplyModifiedPropertiesWithoutUndo();
                Log("Resized " + fixedCount + " saved loadout list(s) on the definition to 5 sets.");
            }
        }

        internal struct Candidate
        {
            public Object mount;
            public string jsonKey;
            public int score;
            public string note;
        }

        // Native weapon mounts whose prefab carries a Turret and a Gun. Mounts already fitted
        // to an aircraft come first, since they are made to ride on one.
        internal static List<Candidate> TurretCandidates()
        {
            var onAircraft = new HashSet<Object>();
            foreach (var d in Definitions())
            {
                var prefab = new SerializedObject(d).FindProperty("unitPrefab");
                var go = prefab != null ? prefab.objectReferenceValue as GameObject : null;
                if (go == null) continue;
                foreach (var m in ComponentsNamed(go, "WeaponManager"))
                {
                    var sets = new SerializedObject(m).FindProperty("hardpointSets");
                    for (var i = 0; sets != null && i < sets.arraySize; i++)
                    {
                        var options = sets.GetArrayElementAtIndex(i).FindPropertyRelative("weaponOptions");
                        for (var j = 0; options != null && j < options.arraySize; j++)
                            if (options.GetArrayElementAtIndex(j).objectReferenceValue != null)
                                onAircraft.Add(options.GetArrayElementAtIndex(j).objectReferenceValue);
                    }
                }
            }
            var list = new List<Candidate>();
            foreach (var mount in WeaponMounts())
            {
                var prefab = MountPrefab(mount);
                if (prefab == null || ComponentsNamed(prefab, "Turret").Count == 0) continue;
                var key = JsonKeyOf(mount);
                var text = (key + " " + mount.name + " " + prefab.name).ToLowerInvariant();
                var score = 0;
                var notes = new List<string>();
                if (onAircraft.Contains(mount)) { score += 10; notes.Add("fitted to an aircraft"); }
                if (ComponentsNamed(prefab, "Gun").Count > 0) { score += 5; notes.Add("has Gun"); }
                if (text.Contains("gun") || text.Contains("cannon") || text.Contains("mm")) score += 3;
                if (text.Contains("missile") || text.Contains("sam") || text.Contains("laser") || text.Contains("rocket")) { score -= 20; notes.Add("not a gun"); }
                list.Add(new Candidate { mount = mount, jsonKey = key, score = score, note = string.Join(", ", notes.ToArray()) });
            }
            list.Sort((a, b) => b.score != a.score ? b.score.CompareTo(a.score) : string.CompareOrdinal(a.jsonKey, b.jsonKey));
            return list;
        }

        static Object PickTurretMount(B36Settings settings)
        {
            if (!string.IsNullOrEmpty(settings.turretWeapon))
            {
                var chosen = FindWeaponMount(settings.turretWeapon);
                if (chosen == null) throw new InvalidOperationException("Turret weapon '" + settings.turretWeapon + "' not found.");
                return chosen;
            }
            var candidates = TurretCandidates();
            if (candidates.Count == 0 || candidates[0].score < 0)
            {
                Log("WARNING: no native turret gun found; the turret set is left empty. Pick one in the B-36 window.");
                return null;
            }
            Log("Turret weapon: " + candidates[0].jsonKey + " (" + candidates[0].note + ").");
            return candidates[0].mount;
        }

        // +1 if the mount's body sits above its attach point, -1 if it hangs below. The
        // turret stations are turned so the body points out of the fuselage either way.
        static float TurretBodySign(Object mount)
        {
            var prefab = mount != null ? MountPrefab(mount) : null;
            if (prefab == null) return 1f;
            var sum = 0f;
            var n = 0;
            foreach (var r in prefab.GetComponentsInChildren<MeshFilter>(true))
            {
                if (r.sharedMesh == null) continue;
                sum += prefab.transform.InverseTransformPoint(r.transform.TransformPoint(r.sharedMesh.bounds.center)).y;
                n++;
            }
            return n > 0 && sum / n < 0f ? -1f : 1f;
        }

        static List<Object> WeaponMounts()
        {
            var list = new List<Object>();
            foreach (var guid in AssetDatabase.FindAssets("t:WeaponMount", new[] { BlueprinterSettings.GameAssetRootFolder, BlueprinterSettings.ModRootFolder }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (BlueprinterAssets.IsStaleGameAssetPath(path)) continue;
                var asset = AssetDatabase.LoadMainAssetAtPath(path);
                if (asset != null && !string.IsNullOrEmpty(JsonKeyOf(asset))) list.Add(asset);
            }
            return list;
        }

        internal static Object FindWeaponMount(string key)
        {
            foreach (var m in WeaponMounts()) if (JsonKeyOf(m) == key) return m;
            return null;
        }

        // The first GameObject a WeaponMount references is the prefab it spawns.
        static GameObject MountPrefab(Object mount)
        {
            var it = new SerializedObject(mount).GetIterator();
            while (it.Next(true))
                if (it.propertyType == SerializedPropertyType.ObjectReference && it.objectReferenceValue is GameObject)
                    return (GameObject)it.objectReferenceValue;
            return null;
        }

        // ------------------------------------------------------------ landing gear

        // Moves each native gear leg so its wheel sits on the B-36 wheel, after
        // F4E-Phantom-NuclearOption's F4EGearIntegration: LandingGear resets the unsprung part
        // from bumpStop every frame, so bumpStop is placed one (travel - radius) above the tyre.
        static void LayOutGear(B36Rig rig, Transform root, Transform frame)
        {
            var gear = ComponentsNamed(root.gameObject, "LandingGear");
            if (gear.Count == 0) throw new InvalidOperationException("The base aircraft has no LandingGear.");
            B36RigLeg nose = null, left = null, right = null;
            foreach (var l in rig.legs)
            {
                if (l.leg == "nose") nose = l;
                else if (l.leg == "mainL") left = l;
                else if (l.leg == "mainR") right = l;
            }
            gear.Sort((a, b) => root.InverseTransformPoint(b.transform.position).z.CompareTo(root.InverseTransformPoint(a.transform.position).z));
            var noseGear = gear[0];
            var lefts = new List<Component>();
            var rights = new List<Component>();
            for (var i = 1; i < gear.Count; i++)
                (root.InverseTransformPoint(gear[i].transform.position).x < 0f ? lefts : rights).Add(gear[i]);

            PlaceLeg(noseGear, B36Rig.V(nose.center), nose.radius, frame, 0);
            var n = 1;
            n = PlaceSide(lefts, left, frame, n);
            PlaceSide(rights, right, frame, n);
            Log("Landing gear: " + gear.Count + " native legs moved onto the B-36 nose and main bogies.");
        }

        // Several native legs on one side share the B-36 bogie, spread over its 1.8 m wheelbase.
        static int PlaceSide(List<Component> legs, B36RigLeg leg, Transform frame, int n)
        {
            legs.Sort((a, b) => b.transform.position.z.CompareTo(a.transform.position.z));
            for (var i = 0; i < legs.Count; i++)
            {
                var dz = legs.Count == 1 ? 0f : Mathf.Lerp(0.9f, -0.9f, i / (float)(legs.Count - 1));
                PlaceLeg(legs[i], B36Rig.V(leg.center) + new Vector3(0f, 0f, dz), leg.radius, frame, n++);
            }
            return n;
        }

        static void PlaceLeg(Component gear, Vector3 rigCenter, float radius, Transform frame, int n)
        {
            var so = new SerializedObject(gear);
            var radiusP = so.FindProperty("wheelRadius");
            var bumpP = so.FindProperty("bumpStop");
            var unsprungP = so.FindProperty("unsprung");
            var travelP = so.FindProperty("suspensionTravel");
            if (radiusP == null || bumpP == null || unsprungP == null || travelP == null)
                throw new InvalidOperationException("LandingGear on " + gear.name + " lacks wheelRadius/bumpStop/unsprung/suspensionTravel; see the audit.");
            var bump = AsTransform(bumpP.objectReferenceValue);
            var unsprung = AsTransform(unsprungP.objectReferenceValue);
            var castP = so.FindProperty("castPoint");
            var cast = castP != null ? AsTransform(castP.objectReferenceValue) : null;
            if (bump == null || unsprung == null) throw new InvalidOperationException("LandingGear on " + gear.name + " has no bumpStop or unsprung part.");
            var travel = travelP.floatValue;
            if (travel <= radius) travelP.floatValue = travel = radius + 0.4f;

            var tyre = frame.TransformPoint(rigCenter);
            // Move the whole leg, then set the suspension points exactly.
            gear.transform.position += tyre - unsprung.position;
            radiusP.floatValue = radius;
            so.ApplyModifiedPropertiesWithoutUndo();
            bump.position = tyre + unsprung.up * (travel - radius);
            unsprung.position = tyre;
            if (cast != null) cast.position = bump.position;

            var key = "Gear_" + n;
            new GameObject(SourcePrefix + key).transform.SetParent(gear.transform, false);
            var rest = new GameObject(RestPrefix + key).transform;
            rest.SetParent(frame, false);
            rest.SetPositionAndRotation(gear.transform.position, gear.transform.rotation);
        }

        static Transform AsTransform(Object o)
        {
            var go = o as GameObject;
            if (go != null) return go.transform;
            var c = o as Component;
            return c != null ? c.transform : null;
        }

        // ------------------------------------------------------------ effects

        // Engine and wingtip effects that would float in the air around the smaller native
        // airframe are moved onto the B-36 jet nozzles and wingtips.
        static void MoveEffects(B36Rig rig, Transform root, Transform frame)
        {
            var engines = new List<Transform>();
            var tips = new List<Transform>();
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (t.IsChildOf(frame)) continue;
                if (t.GetComponent<ParticleSystem>() == null && t.GetComponent<TrailRenderer>() == null) continue;
                if (t.parent != null && (t.parent.GetComponent<ParticleSystem>() != null || t.parent.GetComponent<TrailRenderer>() != null)) continue;
                var n = t.name.ToLowerInvariant();
                if (n.Contains("wingtip") || n.Contains("vortex") || n.Contains("contrail") || n.Contains("vapor")) tips.Add(t);
                else if (n.Contains("exhaust") || n.Contains("nozzle") || n.Contains("afterburner") || n.Contains("engine") || n.Contains("heat")) engines.Add(t);
            }
            var jets = new List<Vector3>();
            foreach (var e in rig.exhausts) if (e.jet) jets.Add(B36Rig.V(e.pos));
            jets.Sort((a, b) => a.x.CompareTo(b.x));
            engines.Sort((a, b) => root.InverseTransformPoint(a.position).x.CompareTo(root.InverseTransformPoint(b.position).x));
            for (var i = 0; i < engines.Count && jets.Count > 0; i++)
            {
                var j = engines.Count == 1 ? 0 : Mathf.RoundToInt(i * (jets.Count - 1) / (float)(engines.Count - 1));
                engines[i].position = frame.TransformPoint(jets[j]);
            }
            foreach (var t in tips)
            {
                var side = root.InverseTransformPoint(t.position).x < 0f ? rig.wingtips.L : rig.wingtips.R;
                t.position = frame.TransformPoint(B36Rig.V(side));
            }
            Log("Effects: " + engines.Count + " engine effects onto " + jets.Count + " jet nozzles, " + tips.Count + " wingtip effects.");
        }

        // ------------------------------------------------------------ helpers

        static Component Single(GameObject root, string typeName)
        {
            var list = ComponentsNamed(root, typeName);
            if (list.Count == 0) throw new InvalidOperationException("The base aircraft has no " + typeName + ".");
            if (list.Count > 1) Log("NOTE: " + list.Count + " " + typeName + " components; using the one on " + list[0].name + ".");
            return list[0];
        }

        static List<Component> ComponentsNamed(GameObject root, string typeName)
        {
            var list = new List<Component>();
            foreach (var c in root.GetComponentsInChildren<Component>(true))
            {
                if (c == null) continue;
                for (var t = c.GetType(); t != null && t != typeof(MonoBehaviour); t = t.BaseType)
                    if (t.Name == typeName) { list.Add(c); break; }
            }
            return list;
        }

        static Transform FindNamed(Transform root, string name)
        {
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
                if (t.name == name) return t;
            throw new InvalidOperationException("Model has no " + name + ".");
        }

        internal static string JsonKeyOf(Object asset)
        {
            return StringField(asset, "jsonKey");
        }

        static string StringField(Object asset, string name)
        {
            var p = new SerializedObject(asset).FindProperty(name);
            return p != null && p.propertyType == SerializedPropertyType.String ? p.stringValue : "";
        }

        static void SetString(SerializedObject so, string name, string value)
        {
            var p = so.FindProperty(name);
            if (p != null) p.stringValue = value;
            else Log("NOTE: definition has no " + name + ".");
        }

        static void SetFloat(SerializedObject so, string name, float value)
        {
            var p = so.FindProperty(name);
            if (p != null) p.floatValue = value;
            else Log("NOTE: definition has no " + name + ".");
        }

        static void SetObject(SerializedObject so, string name, Object value)
        {
            var p = so.FindProperty(name);
            if (p == null) throw new InvalidOperationException("Definition has no " + name + ".");
            p.objectReferenceValue = value;
        }

        static void Log(string line)
        {
            Report.AppendLine(line);
        }

        static void WriteReport(string what)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(ReportPath));
            File.WriteAllText(ReportPath, Report.ToString());
            Debug.Log("[B36] " + what + " report written to " + Path.GetFullPath(ReportPath) + "\n" + Report);
        }

        static void DumpHierarchy(Transform t, int depth)
        {
            var parts = new List<string>();
            foreach (var c in t.GetComponents<Component>())
                if (c != null && !(c is Transform)) parts.Add(c.GetType().Name);
            Log(new string(' ', depth * 2) + t.name + "  [" + string.Join(", ", parts.ToArray()) + "]  layer " + t.gameObject.layer);
            foreach (Transform child in t) DumpHierarchy(child, depth + 1);
        }

        static void DumpWeaponManager(Component manager)
        {
            Log("");
            Log("WeaponManager on " + manager.name + ":");
            var sets = new SerializedObject(manager).FindProperty("hardpointSets");
            for (var i = 0; sets != null && i < sets.arraySize; i++)
            {
                var set = sets.GetArrayElementAtIndex(i);
                Log("  set " + i + " '" + set.FindPropertyRelative("name").stringValue + "', " + Options(set));
                var points = set.FindPropertyRelative("hardpoints");
                for (var j = 0; points != null && j < points.arraySize; j++)
                {
                    var hp = points.GetArrayElementAtIndex(j);
                    var fields = new List<string>();
                    foreach (var c in Children(hp))
                        fields.Add(c.name + "=" + (c.propertyType == SerializedPropertyType.ObjectReference
                            ? (c.objectReferenceValue != null ? c.objectReferenceValue.name + " (" + c.objectReferenceValue.GetType().Name + ")" : "null")
                            : c.isArray ? "[" + c.arraySize + "]" : c.propertyType.ToString()));
                    Log("    hardpoint " + j + ": " + string.Join("; ", fields.ToArray()));
                }
            }
        }

        static void DumpProperties(SerializedObject so, int depth)
        {
            var it = so.GetIterator();
            var enter = true;
            while (it.NextVisible(enter))
            {
                enter = it.depth < 2 && !it.isArray;
                string value;
                switch (it.propertyType)
                {
                    case SerializedPropertyType.String: value = it.stringValue; break;
                    case SerializedPropertyType.Float: value = it.floatValue.ToString(); break;
                    case SerializedPropertyType.Integer: value = it.intValue.ToString(); break;
                    case SerializedPropertyType.Boolean: value = it.boolValue.ToString(); break;
                    case SerializedPropertyType.ObjectReference: value = it.objectReferenceValue != null ? it.objectReferenceValue.name : "null"; break;
                    default: value = it.isArray ? "[" + it.arraySize + "]" : it.propertyType.ToString(); break;
                }
                Log(new string(' ', (depth + it.depth) * 2) + it.propertyPath + " = " + value);
            }
        }
    }
}
