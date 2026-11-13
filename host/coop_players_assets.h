/* The private animation output contract, shared by mods.c and
   nx2/coop_players_assets.py. Define COOP_PLAYER_ASSET(kind, id, path) before including.
   Each actor entry generates a CoopPuppet_<id> from PouchPlayer_<id> and Player_<id>.
   Its animation entry <id>_animation names the private full-body base-pose graph.
   Add both rows for another age; neither consumer has a second age list or file count.
   A '*' selects exactly one source RomFS filename; the output must keep that
   exact name. No version, resource contents or allocation values are shipped. */
COOP_PLAYER_ASSET(actor, Child, "Pack/Actor/CoopPuppet_Child.pack.zs")
COOP_PLAYER_ASSET(animation, Child_animation, "AS/CoopPuppet_Child.root.asb.zs")
COOP_PLAYER_ASSET(table, actor_info, "RSDB/ActorInfo.Product.*.rstbl.byml.zs")
COOP_PLAYER_ASSET(table, game_actor_info, "RSDB/GameActorInfo.Product.*.rstbl.byml.zs")
COOP_PLAYER_ASSET(table, resource_sizes, "System/Resource/ResourceSizeTable.*.rsizetable.zs")
